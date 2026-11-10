"""FastAPI 入口 —— 提供文档索引与 RAG 问答的 REST API。

运行方式（开发环境）:
    uvicorn app.main:app --reload --port 8000

路由按业务域拆分到 app/routers/ 下的独立模块，本文件只保留：
    - app 实例与 Prometheus 指标
    - 全局鉴权/审计中间件
    - 启动预热事件
    - /metrics 与 /dashboard/stats 两个无域归属的端点
    - 7 个 include_router 挂载
"""

import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.core_Qdrant import refresh_docs_meta_cache, warmup_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Prometheus 指标（可选依赖）────────────────────────────

try:
    import prometheus_client
    from prometheus_client import Counter, Histogram, Gauge, generate_latest

    _METRICS = {
        "requests_total": Counter(
            "rag_requests_total", "Total requests",
            ["endpoint"],
        ),
        "request_latency_seconds": Histogram(
            "rag_request_latency_seconds", "Request latency",
            ["endpoint"],
        ),
        "retrieval_latency_seconds": Histogram(
            "rag_retrieval_latency_seconds", "Retrieval latency",
        ),
        "llm_latency_seconds": Histogram(
            "rag_llm_latency_seconds", "LLM call latency",
            ["provider"],
        ),
        "documents_total": Gauge(
            "rag_documents_total", "Total indexed documents",
        ),
        "chunks_total": Gauge(
            "rag_chunks_total", "Total chunks",
        ),
    }
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _METRICS = {}
    _PROMETHEUS_AVAILABLE = False


def _track_request(endpoint: str, latency: float) -> None:
    """记录请求指标（Prometheus 可用时）。"""
    if not _PROMETHEUS_AVAILABLE:
        return
    _METRICS["requests_total"].labels(endpoint=endpoint).inc()
    _METRICS["request_latency_seconds"].labels(endpoint=endpoint).observe(latency)


app = FastAPI(title="RAG API", version="0.4")


# ── Prometheus /metrics 端点 ────────────────────────────────


@app.get("/metrics")
def metrics_endpoint():
    """Prometheus 指标端点。

    提供请求计数、延迟分布、文档数量等指标。
    依赖 prometheus_client 库（可选，未安装返回 503）。
    """
    if not _PROMETHEUS_AVAILABLE:
        return PlainTextResponse(
            "# prometheus_client not installed\nrag_info 0\n",
            status_code=503,
        )
    # 更新文档数量指标
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        col = settings.qdrant_collection
        existing = {c.name for c in client.get_collections().collections}
        if col in existing:
            count = client.count(collection_name=col, exact=True).count
            _METRICS["documents_total"].set(count)
    except Exception:
        pass
    return PlainTextResponse(generate_latest(), media_type="text/plain")


# ── 工作台统计端点（无自然域归属，留在此处）────────────────────


@app.get("/dashboard/stats")
async def dashboard_stats_endpoint():
    """工作台统计数据（未闭环/超期/本月新增/材料总数/等级分布/超期列表/最近材料）。"""
    from app.services.svc_Ledger.dashboard_service import get_dashboard_stats

    return {"success": True, "data": await get_dashboard_stats()}


# ── API Key 鉴权 + 请求计时中间件 ───────────────────────────


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """全局中间件：JWT / X-API-Key 鉴权 + 请求计时 + 审计日志。

    V2 权限模型:
        - JWT claim 自带 is_admin/name，解码后无需查库即可填充 request.state.user
        - is_admin 仅额外放行账号管理接口；业务数据全员平等
        - X-API-Key 兼容内网/管理员调用，scope=ALL

    公开路径（无需鉴权）:
        /  /health  /metrics  /auth/login
    """
    path = request.url.path.rstrip("/")
    start = time.time()

    # 公开路径（/auth/me 的 GET 用于登录后取当前用户，但需要 JWT，不算公开；
    # 此处 public_paths 只列真正无需鉴权的）
    public_paths = ("", "/health", "/metrics", "/auth/login")
    is_public = path in public_paths or path.startswith("/docs") or path.startswith("/openapi")

    request.state.user = None
    request.state.scope = None

    if not is_public:
        # 优先 JWT（claim 自带 is_admin/name，无需查库）
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from app.services.svc_Auth.auth_service import decode_token
            from app.services.svc_Auth.user_service import compute_scope

            claim = decode_token(token)
            if claim:
                try:
                    staff_id = int(claim.get("sub", 0))
                    # 用 claim 直接构造轻量 user 对象，避免每次请求查库
                    from app.db.models import Staff
                    user = Staff(
                        id=staff_id,
                        username=claim.get("username", ""),
                        name=claim.get("name", ""),
                        is_admin=bool(claim.get("is_admin", False)),
                    )
                    request.state.user = user
                    request.state.scope = compute_scope(user)
                except Exception:
                    pass  # claim 解析异常，降级到 X-API-Key
            else:
                # Bearer token 存在但解码失败（过期/签名无效/格式错误）
                # → 立即 401，让前端响应拦截器清登录态跳登录页。
                # 不再静默放行走 _require_admin 返回 403（前端无法识别为鉴权失败）
                return JSONResponse(
                    status_code=401,
                    content={"detail": "token 已过期或无效，请重新登录"},
                )

        # X-API-Key 兼容
        if request.state.user is None and settings.rag_api_key:
            key = request.headers.get("X-API-Key")
            if key == settings.rag_api_key:
                # 管理员/内网调用，scope=ALL
                request.state.scope = {"is_admin": True, "allowed_enterprises": "all"}
            else:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key / JWT token"},
                )
        elif request.state.user is None and not settings.rag_api_key:
            # 未配置 RAG_API_KEY 且无 JWT：开发模式放行（scope=None）
            pass

    response = await call_next(request)

    # 记录请求指标 + 审计日志
    if not is_public:
        latency = time.time() - start
        _track_request(path, latency)

        # 审计策略（决策三：只记写操作 + 登录事件，读请求一律不记）：
        #   - GET/HEAD/OPTIONS 不记录
        #   - 成功的写操作由业务层显式记录语义动作（enterprise.create 等），不重复记
        #   - 失败的写操作（4xx/5xx）在此兜底记录，保留失败尝试痕迹
        from app.services.svc_Auth.audit_service import log_action_async
        is_write = request.method.upper() in ("POST", "PUT", "PATCH", "DELETE")
        if is_write and response.status_code >= 400:
            log_action_async(
                action=f"http.{request.method.lower()}.{path}",
                staff_id=getattr(request.state.user, "id", None) if request.state.user else None,
                ip_address=request.client.host if request.client else None,
                latency_ms=int(latency * 1000),
                status="failed",
            )

    return response


# ── 启动预热 ──────────────────────────────────────────────


@app.on_event("startup")
async def startup_warmup():
    """应用启动时后台预热：DB 初始化 + 模型加载 + 文档列表缓存构建。

    模型加载（BGE-M3 + Reranker）和文档列表缓存构建都在后台线程并行执行，
    不影响 FastAPI 立即接受请求。首次请求时如果预热未完成会自动阻塞等待。
    """
    logger = logging.getLogger(__name__)

    # PostgreSQL 建表 + 种子数据初始化
    try:
        from app.db.database import init_db
        from app.db.seed import seed_database
        await init_db()
        await seed_database()
        logger.info("PostgreSQL 初始化完成")
    except Exception as e:
        logger.warning("PostgreSQL 初始化失败（业务层不可用）: %s", e)

    logger.info("启动预热：开始后台加载模型...")
    warmup_models(blocking=False)

    # 预热文档列表缓存（后台线程，不需要等待模型就绪）
    threading.Thread(target=refresh_docs_meta_cache, daemon=True).start()


# ── 路由挂载 ──────────────────────────────────────────────

from app.routers import (
    admin,
    auth,
    chat,
    doc_export,
    enterprises,
    factory_in_factory,
    hazardous_chemicals,
    inspections,
    materials,
    rag,
)

app.include_router(rag.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(enterprises.router)
app.include_router(materials.router)
app.include_router(inspections.router)
app.include_router(factory_in_factory.router)
app.include_router(hazardous_chemicals.router)
app.include_router(chat.router)
app.include_router(doc_export.router)
