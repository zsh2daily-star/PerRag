"""FastAPI 入口 —— 提供文档索引与 RAG 问答的 REST API。

运行方式（开发环境）:
    uvicorn app.main:app --reload --port 8000

主要接口:
    GET  /health             健康检查
    GET  /index/info         查看索引配置
    GET  /index/preview      预览目录中的文件列表
    POST /index/directory    索引目录（同步，等待完成）
    POST /index/directory/async  索引目录（异步，后台执行）
    POST /ask                提问（检索 + 大模型生成回答）
    GET  /v1/models          OpenAI 兼容模型列表
    POST /v1/chat/completions OpenAI 兼容聊天补全（供 Open WebUI 接入）
"""

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.config import resolve_api_key, settings
from app.indexer import DocumentIndexer, DirectoryIndexResult
from app.parser import SUPPORTED_EXTENSIONS, collect_files
from app.retriever import get_retriever

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


# ── API Key 鉴权 + 请求计时中间件 ───────────────────────────


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """全局中间件：API Key 鉴权 + 请求计时。

    当 .env 中配置了 RAG_API_KEY 时，所有请求必须携带
    X-API-Key header 且值匹配。未配置则不启用。

    公开路径（无需鉴权）:
        /  /health  /metrics
    """
    path = request.url.path.rstrip("/")
    start = time.time()

    if path not in ("", "/health", "/metrics"):
        if settings.rag_api_key:
            key = request.headers.get("X-API-Key")
            if not key or key != settings.rag_api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-API-Key header"},
                )

    response = await call_next(request)

    # 记录请求指标
    if path not in ("", "/health", "/metrics"):
        latency = time.time() - start
        _track_request(path, latency)

    return response


# ── 启动预热 ──────────────────────────────────────────────


@app.on_event("startup")
async def startup_warmup():
    """应用启动时后台预热：模型加载 + 文档列表缓存构建。

    模型加载（BGE-M3 + Reranker）和文档列表缓存构建都在后台线程并行执行，
    不影响 FastAPI 立即接受请求。首次请求时如果预热未完成会自动阻塞等待。
    """
    from app.models import warmup_models

    logger = logging.getLogger(__name__)
    logger.info("启动预热：开始后台加载模型...")
    warmup_models(blocking=False)

    # 预热文档列表缓存（后台线程，不需要等待模型就绪）
    threading.Thread(target=build_doc_list_cache, daemon=True).start()


# ── 全局单例 ──────────────────────────────────────────────

_indexer: DocumentIndexer | None = None
_lock = threading.Lock()


def get_indexer() -> DocumentIndexer:
    """获取全局唯一的 DocumentIndexer 单例（线程安全的 double-checked locking）。"""
    global _indexer
    if _indexer is None:
        with _lock:
            if _indexer is None:
                _indexer = DocumentIndexer()
    return _indexer


def _run_index_directory(
    directory: Path | None,
    recursive: bool,
    replace: bool,
    skip_existing: bool = False,
    collection: str | None = None,
    task_id: str | None = None,
) -> DirectoryIndexResult:
    """执行目录索引任务 —— 对 Indexer.index_directory() 的薄封装。

    提取为独立函数是为了同时被同步端点 (/index/directory)
    和异步后台任务 (/index/directory/async) 复用。

    参数:
        task_id: 异步任务 ID，传入后 indexer 会实时更新进度到 _task_status
    """
    result = get_indexer().index_directory(
        directory=directory,
        recursive=recursive,
        replace=replace,
        skip_existing=skip_existing,
        collection=collection,
        task_id=task_id,
    )
    # 索引完成后文档列表变了，刷新缓存
    if result.indexed > 0:
        threading.Thread(target=build_doc_list_cache, daemon=True).start()
    return result


# ── 请求/响应数据模型 ──────────────────────────────────────


class IndexDirectoryRequest(BaseModel):
    """目录索引请求模型。"""
    directory: str | None = Field(
        default=None,
        description="本地目录路径，默认使用 IMPORT_DIR 环境变量的值",
    )
    collection: str | None = Field(
        default=None,
        description="目标 Qdrant collection，默认使用 QDRANT_COLLECTION 环境变量的值",
    )
    recursive: bool = True
    replace: bool = Field(
        default=False,
        description="是否强制重建（先删除已有同源数据再索引），默认追加",
    )
    skip_existing: bool = Field(
        default=False,
        description="是否跳过已导入的文件（按 source 路径判断），默认不跳过",
    )


class IndexDirectoryResponse(BaseModel):
    """目录索引响应模型。"""
    directory: str
    total_files: int
    indexed: int
    failed: int
    skipped: int
    total_chunks: int
    files: list[dict]


class AskRequest(BaseModel):
    """提问请求 —— 支持请求级切换 LLM，不传则用 .env 默认配置。

    llm_provider: ollama（本地）/ api（OpenAI 兼容）
    llm_model:    模型名，如 qwen3:8b / deepseek-chat
    llm_api_base: API 地址（仅 provider=api 时有效）
    llm_api_key:  API 密钥（仅 provider=api 时有效）
    filters:      可选 metadata 过滤，如 {"source_format":"pdf","filename":"*招商*"}
    """
    query: str
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_base: str | None = None
    llm_api_key: str | None = None
    filters: dict[str, str] | None = None


class AskResponse(BaseModel):
    """问答响应模型：query + answer + sources。"""
    query: str
    answer: str
    sources: list[dict]


# ── OpenAI 兼容数据模型 ────────────────────────────────────


class ChatMessage(BaseModel):
    """OpenAI 兼容的消息对象。

    支持 tool calling 的三种消息类型：
    - 普通消息: {"role": "user", "content": "你好"}
    - assistant 工具调用: {"role": "assistant", "tool_calls": [...], "content": null}
    - tool 返回结果: {"role": "tool", "tool_call_id": "call_xxx", "content": "结果"}
    """
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None   # assistant 消息的工具调用列表
    tool_call_id: str | None = None        # tool 消息对应的调用 ID
    name: str | None = None                # tool 消息发送者名称（可选）


class ChatCompletionRequest(BaseModel):
    """OpenAI 兼容的聊天补全请求（简单 RAG，非流式）。

    提取最后一条 user 消息作为查询，检索知识库后交给 LLM 生成回答。
    """
    model: str = ""
    messages: list[ChatMessage]
    temperature: float | None = 0.3
    stream: bool = False
    max_tokens: int | None = None
    tools: list[dict] | None = None        # OpenAI 格式的工具定义列表（简单 RAG 忽略）
    tool_choice: str | dict | None = None  # 简单 RAG 忽略
    filters: dict[str, str] | None = None  # metadata 过滤条件，如 {"source_format": "pdf"}


class ChatCompletionChoice(BaseModel):
    """OpenAI 兼容的补全选项（一条 assistant 回复）。"""
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI 兼容的聊天补全响应。"""
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]


class ModelItem(BaseModel):
    """OpenAI 兼容的模型项（/v1/models 返回的元素）。"""
    id: str
    object: str = "model"
    created: int
    owned_by: str = "ollama"


class ModelListResponse(BaseModel):
    """OpenAI 兼容的模型列表响应（/v1/models）。"""
    object: str = "list"
    data: list[ModelItem]


# ── API 接口 ──────────────────────────────────────────────


@app.get("/")
def root():
    """API 基本信息。"""
    return {"message": "RAG API is running"}


@app.get("/health")
def health():
    """健康检查，返回 {"status": "ok"}。"""
    return {"status": "ok"}


@app.get("/index/info")
def index_info():
    """查看索引配置（嵌入模型、Qdrant 地址、支持的文件类型等）。"""
    return {
        "import_dir": str(settings.import_dir),
        "qdrant_host": settings.qdrant_host,
        "qdrant_port": settings.qdrant_port,
        "qdrant_collection": settings.qdrant_collection,
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
    }


@app.get("/index/preview")
def index_preview(directory: str | None = None, recursive: bool = True):
    """预览待索引的文件列表（不实际解析/写入）。"""
    dir_path = Path(directory) if directory else settings.import_dir
    if not dir_path.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在: {dir_path}")
    files = collect_files(dir_path, recursive=recursive,
                           exclude_paths=settings.exclude_path_list)
    return {
        "directory": str(dir_path.resolve()),
        "total_files": len(files),
        "files": [str(f) for f in files],
    }


@app.post("/index/directory", response_model=IndexDirectoryResponse)
def index_directory(body: IndexDirectoryRequest):
    """同步索引指定目录（阻塞等待完成，适合少量文件）。"""
    dir_path = Path(body.directory) if body.directory else settings.import_dir
    if not dir_path.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在: {dir_path}")

    result = _run_index_directory(
        dir_path, body.recursive, body.replace, body.skip_existing, body.collection,
    )
    return IndexDirectoryResponse(**result.to_dict())


@app.post("/index/directory/async")
def index_directory_async(body: IndexDirectoryRequest, background_tasks: BackgroundTasks):
    """异步索引目录（后台执行，立即返回 task_id）。"""
    dir_path = Path(body.directory) if body.directory else settings.import_dir
    if not dir_path.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在: {dir_path}")

    # 生成任务 ID 并初始化状态
    task_id = uuid.uuid4().hex[:12]
    from app.indexer import _set_task_status
    _set_task_status(task_id, status="pending")

    background_tasks.add_task(
        _run_index_directory,
        dir_path,
        body.recursive,
        body.replace,
        body.skip_existing,
        body.collection,
        task_id,  # 传入 task_id，indexer 会实时更新进度
    )
    files = collect_files(dir_path, recursive=body.recursive,
                           exclude_paths=settings.exclude_path_list)
    return {
        "message": "索引任务已在后台启动",
        "task_id": task_id,
        "directory": str(dir_path.resolve()),
        "total_files": len(files),
    }
def ask(body: AskRequest):
    """RAG 问答 —— 检索 + LLM 生成。"""
    provider = body.llm_provider or settings.llm_provider
    model = body.llm_model or (
        settings.api_default_model if provider == "api"
        else settings.ollama_default_model
    )
    api_base = body.llm_api_base or settings.api_default_base
    api_key = body.llm_api_key or resolve_api_key(model)

    retriever = get_retriever()
    result = retriever.ask(
        body.query,
        filters=body.filters,
        llm_provider=provider,
        llm_model=model,
        llm_api_base=api_base,
        llm_api_key=api_key,
    )
    return AskResponse(
        query=body.query,
        answer=result["answer"],
        sources=result["sources"],
    )


# ── 文档列表缓存 ──────────────────────────────────────────

# 启动时从 Qdrant 拉取并缓存，后续请求直接用缓存不查 Qdrant。
# 索引/删除操作后自动刷新，避免每次 Tool 透传都 scroll 全库。
_doc_list_cache: list[dict] | None = None
_doc_list_cache_lock = threading.Lock()


def build_doc_list_cache(collection: str | None = None) -> list[dict]:
    """重新构建文档列表缓存 —— 从 Qdrant scroll 全量数据。

    启动时和索引变更后调用。缓存 miss 时 get_cached_doc_list 也会自动构建。
    """
    global _doc_list_cache
    with _doc_list_cache_lock:
        docs = _list_docs_from_qdrant(collection)
        _doc_list_cache = docs
        logger = logging.getLogger(__name__)
        logger.info("文档列表缓存已刷新: %d 个文档", len(docs))
        return docs


def get_cached_doc_list(force_refresh: bool = False) -> list[dict]:
    """获取缓存的文档列表。首次调用/缓存为空时自动从 Qdrant 构建。

    与 _list_docs_from_qdrant 的区别：
    - 首次构建后后续调用走缓存，不查 Qdrant
    - 适用于高频调用场景（每次 Tool 透传请求）
    - force_refresh=True 可强制重建（索引变更后调用）
    """
    global _doc_list_cache
    if _doc_list_cache is None or force_refresh:
        return build_doc_list_cache()
    return _doc_list_cache


# ── 文档列表查询 ──────────────────────────────────────────


def _list_docs_from_qdrant(collection: str | None = None) -> list[dict]:
    """从 Qdrant 提取指定 collection 的去重文档列表。

    遍历所有点的 payload，按 filename 去重，
    返回文件名、来源路径、chunk 数量等信息。

    实现细节：
    - 使用 scroll（游标分页）而非 search，因为不需要语义匹配
    - 每批取 100 条，最多 500 批（= 50,000 条上限），避免无限循环
    - 按 filename 去重并统计每个文件的 chunk 数
    """
    from qdrant_client import QdrantClient

    col = collection or settings.qdrant_collection
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    # 检查 collection 是否存在（刚启动时可能还没创建）
    existing = {c.name for c in client.get_collections().collections}
    if col not in existing:
        return []

    doc_map: dict[str, dict] = {}
    offset = None  # scroll 游标，None 表示从头开始

    # 分批遍历所有点（最多 500 批 × 100 = 50,000 条）
    for _ in range(500):
        resp = client.scroll(
            collection_name=col,
            limit=100,
            offset=offset,
            with_payload=["filename", "source"],  # 只取需要的字段，省带宽
        )
        points = resp[0]
        if not points:
            break  # 没有更多数据了
        for p in points:
            fn = p.payload.get("filename", "unknown") if p.payload else "unknown"
            src = p.payload.get("source", "") if p.payload else ""
            if fn not in doc_map:
                doc_map[fn] = {"filename": fn, "source": src, "chunks": 0}
            doc_map[fn]["chunks"] += 1
        offset = resp[1]  # 下一页的游标
        if offset is None:
            break  # 已遍历完所有数据

    return sorted(doc_map.values(), key=lambda x: x["filename"])


@app.post("/list-docs")
def list_docs(collection: str | None = None):
    """列出知识库中的文档列表。

    请求体（可选）:
        {"collection": "rag_documents"}

    返回按文件名排序的去重文档列表，包含 chunk 数量。
    """
    docs = get_cached_doc_list() if collection is None else _list_docs_from_qdrant(collection)
    return {
        "collection": collection or settings.qdrant_collection,
        "total_docs": len(docs),
        "total_chunks": sum(d["chunks"] for d in docs),
        "documents": docs,
    }


# ── 文档删除 ──────────────────────────────────────────────


class DeleteDocumentRequest(BaseModel):
    """删除文档请求 —— 按文件名匹配（不关心路径变更）。"""
    filename: str = Field(description="文件名，如 'report.pdf'，匹配 payload.filename 字段")
    collection: str | None = Field(
        default=None,
        description="目标 collection，默认使用 QDRANT_COLLECTION",
    )


@app.delete("/index/document")
def delete_document(body: DeleteDocumentRequest):
    """从知识库中按文件名删除指定文档。

    同时删除 dense 和 sparse 两个 collection 中该文件的所有向量点。
    不会删除磁盘上的原始文件 —— 只是解除索引。

    示例:
        curl -X DELETE http://localhost:8000/index/document \
          -H "Content-Type: application/json" \
          -d '{"filename": "old.pdf"}'
    """
    col = body.collection or settings.qdrant_collection
    indexer = get_indexer()
    # 临时覆写 collection（indexer 内部用 _collection_override）
    indexer._collection_override = col
    indexer._delete_by_filename(body.filename)
    indexer._delete_sparse_by_filename(body.filename)
    # 文档列表变了，刷新缓存
    threading.Thread(target=build_doc_list_cache, daemon=True).start()
    return {
        "message": "已删除",
        "filename": body.filename,
        "collection": col,
    }


# ── 异步索引状态查询 ────────────────────────────────────────


@app.get("/index/status/{task_id}")
def index_status(task_id: str):
    """查询异步索引任务的进度。

    返回:
        {"status": "pending"|"running"|"done", "progress": "15/50",
         "current_file": "report.pdf", "indexed": 14, "failed": 0, "skipped": 1}

    示例:
        curl http://localhost:8000/index/status/abc123def456
    """
    from app.indexer import get_task_status

    status = get_task_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"任务不存在或已过期: {task_id}")
    return status


# ── 文档预览（分块预览）─────────────────────────────────────


class PreviewFileRequest(BaseModel):
    """预览请求 —— 展示文件解析和分块结果。"""
    file_path: str = Field(description="服务器上的文件绝对路径")
    chunk_size: int | None = Field(
        default=None, description="覆盖默认 CHUNK_SIZE"
    )
    chunk_overlap: int | None = Field(
        default=None, description="覆盖默认 CHUNK_OVERLAP"
    )


@app.post("/index/preview-file")
def preview_file(body: PreviewFileRequest):
    """预览单个文件的解析和分块结果 —— 不写入 Qdrant。

    在正式索引前，可以用此接口检查：
    - 文件是否能被正确解析
    - 解析出的文本质量如何
    - 会被切成多少个 chunk
    - 前几个 chunk 的内容是什么（采样）

    返回:
        {
            "filename": "report.pdf",
            "parser": "pypdf",
            "source_format": "pdf",
            "page_count": 12,
            "total_chunks": 24,
            "chunk_size": 512,
            "chunk_overlap": 50,
            "total_chars": 8234,
            "sample_chunks": ["前200字...", "前200字...", ...]
        }

    示例:
        curl -X POST http://localhost:8000/index/preview-file \
          -H "Content-Type: application/json" \
          -d '{"file_path": "/app/data/uploads/report.pdf"}'
    """
    from app.parser import parse_file

    path = Path(body.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"路径不是文件: {path}")

    # 解析文档
    try:
        parsed = parse_file(path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"文档解析失败: {e}")

    # 分块（与索引阶段使用相同的参数和分块策略）
    chunk_size = body.chunk_size or settings.chunk_size
    chunk_overlap = body.chunk_overlap or settings.chunk_overlap

    from llama_index.core import Document as LlamaDocument
    from app.indexer import _create_splitter

    doc = LlamaDocument(text=parsed.text, metadata={
        "source": str(path.resolve()),
        "filename": path.name,
        "source_format": parsed.source_format,
    })
    splitter = _create_splitter(chunk_size, chunk_overlap, method=settings.chunk_method)
    nodes = splitter.get_nodes_from_documents([doc])

    # 采样前 5 个 chunk 做预览（每个截取前 200 字符）
    samples = [n.text[:200] for n in nodes[:5]]

    return {
        "filename": path.name,
        "parser": settings.pdf_parser if parsed.source_format == "pdf" else parsed.source_format,
        "source_format": parsed.source_format,
        "page_count": parsed.page_count,
        "total_chunks": len(nodes),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "total_chars": len(parsed.text),
        "sample_chunks": samples,
        "metadata": parsed.metadata,
    }


@app.post("/aggregate", response_model=AskResponse)
def aggregate_endpoint(body: AskRequest, collections: list[str] | None = None):
    """跨知识库聚合统计：多轮检索 → 汇总去重 → LLM 全局分析。

    与 /ask 的区别：
    - /ask 只检索少量相关片段做回答
    - /aggregate 会多轮检索更多文档，汇总后做全局统计/趋势分析

    适用场景：
    - "知识库中有哪些共同主题？"
    - "总结所有文档中关于机器学习的观点"
    - "这些报告中有哪些统计数据？"
    """
    result = get_retriever().aggregate(
        body.query,
        collection=settings.qdrant_collection,
        collections=collections,
        filters=body.filters,
        llm_provider=body.llm_provider,
        llm_model=body.llm_model,
        llm_api_base=body.llm_api_base,
        llm_api_key=body.llm_api_key,
    )
    return AskResponse(**result)


# ── OpenAI 兼容端点（供 Open WebUI 接入）────────────────────


def _discover_ollama_models() -> list[str]:
    """从 Ollama 获取本地已拉取的模型列表。

    调用 Ollama 的 /api/tags 端点获取所有本地模型名。
    如果 Ollama 不可达，降级返回 .env 中配置的默认模型名，
    确保 /v1/models 和 /v1/chat/completions 即使在 Ollama 挂了
    的情况下也能给出一个可用的 fallback。
    """
    try:
        resp = httpx.get(
            f"{settings.ollama_base_url}/api/tags",
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return [m["name"] for m in models if "name" in m]
    except Exception:
        logger = logging.getLogger(__name__)
        logger.warning("无法从 Ollama 获取模型列表")
        return [settings.ollama_default_model]


def _build_models_list() -> list[dict[str, Any]]:
    """构建 OpenAI 兼容的模型列表。

    合并两个来源：
    1. Ollama 本地模型（通过 /api/tags 动态发现）
    2. API 远程模型（从 .env 的 API_DEFAULT_MODEL 读取）

    去重逻辑：如果远程模型已经在 Ollama 列表中出现（比如同名），
    只保留一份，避免 /v1/models 返回重复条目。
    """
    models: list[dict[str, Any]] = []
    now = int(time.time())

    # 1. Ollama 本地模型（排除 hf.co 等远程源路径，只保留别名）
    for name in _discover_ollama_models():
        if name.startswith("hf.co/"):
            continue
        models.append({
            "id": name,
            "object": "model",
            "created": now,
            "owned_by": "ollama",
        })

    # 2. API 远程模型（如果配置了）
    if settings.api_default_model:
        # 避免重复：Ollama 中没有同名模型才添加
        if not any(m["id"] == settings.api_default_model for m in models):
            models.append({
                "id": settings.api_default_model,
                "object": "model",
                "created": now,
                "owned_by": "api",
            })

    return models


@app.get("/v1/models", response_model=ModelListResponse)
def list_models():
    """OpenAI 兼容的模型列表端点。

    Open WebUI 通过此端点发现可用模型。
    """
    return ModelListResponse(data=[ModelItem(**m) for m in _build_models_list()])


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(body: ChatCompletionRequest):
    """OpenAI 兼容端点 —— 简单 RAG 问答（检索 + LLM 生成，无 agent 循环）。

    供 openwebui 等 OpenAI 兼容客户端使用。提取用户最后一条消息作为查询，
    检索知识库后交给 LLM 生成回答。
    """
    # 模型解析
    model = body.model or settings.api_default_model
    ollama_models = _discover_ollama_models()
    if model in ollama_models:
        provider = "ollama"
        api_base = None
        api_key = None
    else:
        provider = "api"
        api_base = settings.api_default_base
        api_key = resolve_api_key(model)

    # 提取用户最后一条消息作为查询
    user_msgs = [m.content for m in body.messages if m.role == "user" and m.content]
    query = user_msgs[-1].strip() if user_msgs else ""

    if not query:
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model,
            choices=[ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=""),
                finish_reason="stop",
            )],
        )

    # 检索 + 生成
    retriever = get_retriever()
    result = retriever.ask(
        query,
        filters=body.filters,
        llm_provider=provider,
        llm_model=model,
        llm_api_base=api_base,
        llm_api_key=api_key,
    )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=model,
        choices=[ChatCompletionChoice(
            message=ChatMessage(role="assistant", content=result["answer"]),
            finish_reason="stop",
        )],
    )
