"""查询路由模块 —— 启动时分析 Qdrant 生成概括，提问时判断是否需要知识库。

两层架构：
  启动时（只跑一次）:
    1. 遍历 Qdrant 中所有 collection（过滤掉 _sparse 后缀）
    2. 采样每个 collection 的文档名 + 文本片段
    3. 调 qwen3:8b 生成该 collection 的内容概括
    4. 缓存所有概括到内存

  每次提问:
    1. 取出缓存的概括 + 用户问题
    2. 拼接路由提示词 → 调 LLM → 返回 JSON
       {"action": "chat"|"list_docs"|"aggregate"|"search", "collection": "xxx", "reason": "..."}
    3. 上游根据结果选择合适的处理路径
"""

import json
import logging
import threading

import httpx
from qdrant_client import QdrantClient

from app.config import settings

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """你是一个查询路由器。根据下面的知识库概括，判断对用户问题应该采取什么行动。

知识库概括:
{summaries}

用户问题: {query}

请严格按以下 JSON 格式回答，不要加其他文字:
{{"action": "chat"或"list_docs"或"aggregate"或"search", "collection": "collection名称或null", "reason": "一句话"}}

行动说明（按优先级判断）:
- action="list_docs": 用户想列出知识库有哪些文件，如"有哪些文档""列出文件"
- action="aggregate": 用户想全面了解某个主题/对象在知识库中的全部或大量信息，
  如"汇总XX公司的信息""知识库中关于XX的所有内容""XX在知识库中都有什么记载"
  "知识库中有哪些关于XX的信息""介绍XX""帮我全面了解XX"。
  特点：答案需要综合大量文档，不是一两句话能说清的，需要遍历式地收集信息。
- action="search": 用户问一个具体的事实性问题，答案集中在少数相关段落即可，
  如"XX公司哪年成立""XX的定义是什么""XX和YY有什么区别"。
  特点：精确问答，不需要遍历所有文档。
- action="chat": 闲聊或常识问题，不需要知识库，collection 填 null"""


def _build_collection_summary(collection: str) -> str:
    """为一个 collection 生成内容概括。

    流程：
    1. 从 Qdrant 采样最多 30 个文档名
    2. 采样 3 段文本片段（最多 200 字符）
    3. 组成提示词 → qwen3:8b 生成 2-3 句概括
    """
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    filenames: set[str] = set()
    offset = None
    for _ in range(5):
        resp = client.scroll(
            collection_name=collection, limit=20, offset=offset,
            with_payload=["filename", "text"],
        )
        points = resp[0]
        if not points:
            break
        for p in points:
            fn = p.payload.get("filename", "")
            if fn:
                filenames.add(fn)
        offset = resp[1]
        if offset is None or len(filenames) >= 30:
            break

    sample = client.scroll(collection_name=collection, limit=3, with_payload=["text"])
    texts = [p.payload.get("text", "")[:200] for p in sample[0]]

    doc_list = "\n".join(f"- {f}" for f in sorted(filenames)[:20])
    text_snippets = "\n".join(f"- {t}" for t in texts if t)
    prompt = f"""以下是知识库 "{collection}" 的文档信息和内容片段。请用 2-3 句话概括这个知识库的内容范围和主题。

文档列表（部分）:
{doc_list}

内容片段:
{text_snippets}

请概括:"""

    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_default_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", f"知识库 {collection}（概括生成失败）")
    except Exception:
        return f"知识库 {collection}（包含 {len(filenames)} 个文档）"


def build_all_summaries() -> dict[str, str]:
    """为 Qdrant 中所有 collection 生成概括。"""
    summaries: dict[str, str] = {}
    try:
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        cols = {c.name for c in client.get_collections().collections}
        dense_cols = sorted(c for c in cols if not c.endswith("_sparse"))

        for col in dense_cols:
            logger.info("[router] 为 collection %s 生成概括...", col)
            summaries[col] = _build_collection_summary(col)

        if not summaries:
            summaries[settings.qdrant_collection] = "（暂无知识库数据）"
    except Exception as e:
        logger.warning("[router] 概括生成失败: %s", e)
        summaries[settings.qdrant_collection] = "（无法连接 Qdrant）"

    logger.info("[router] 已生成 %d 个 collection 概括", len(summaries))
    return summaries


_summaries: dict[str, str] = {}
_lock = threading.Lock()


def get_summaries() -> dict[str, str]:
    """获取缓存的 collection 概括。首次调用时自动生成。"""
    global _summaries
    if not _summaries:
        with _lock:
            if not _summaries:
                _summaries = build_all_summaries()
    return _summaries


def _call_router_llm(prompt: str, llm_provider: str, llm_model: str,
                     llm_api_base: str = "", llm_api_key: str = "") -> str:
    """调用大模型做路由判断，自动适配 ollama 和 api 两种后端。

    API 路径（OpenAI 兼容）:
        用 response_format={"type":"json_object"} 确保返回合法 JSON

    Ollama 路径（本地）:
        用 format="json" 约束输出为 JSON 格式
        调 /api/generate 而非 /api/chat（路由判断只需要单条 prompt）
    """
    # ── API 远程后端（DeepSeek / OpenAI / Groq 等）─────────
    if llm_provider == "api":
        url = f"{llm_api_base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if llm_api_key:
            headers["Authorization"] = f"Bearer {llm_api_key}"
        payload = {
            "model": llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,  # 零温度确保路由判断稳定、可复现
            "response_format": {"type": "json_object"},  # 强制 JSON 输出
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── Ollama 本地后端 ─────────────────────────────────────
    else:
        # /api/generate 比 /api/chat 更适合单条 prompt 场景
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": llm_model or settings.ollama_default_model, "prompt": prompt,
                  "stream": False, "format": "json"},  # Ollama JSON 模式
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "{}")


def route_query(query: str, llm_provider: str | None = None,
                llm_model: str | None = None,
                llm_api_base: str | None = None,
                llm_api_key: str | None = None) -> dict:
    """一次 LLM 调用 → 返回行动指令。

    返回: {"action": "chat"|"list_docs"|"aggregate"|"search", "collection": str|None, "reason": str}
    """
    provider = llm_provider or settings.llm_provider
    model = llm_model or (settings.api_default_model if provider == "api"
                          else settings.ollama_default_model)
    summaries = get_summaries()
    summary_text = "\n\n".join(f"**{k}**: {v}" for k, v in summaries.items())

    prompt = ROUTER_PROMPT.format(summaries=summary_text, query=query)

    try:
        resp_text = _call_router_llm(
            prompt, provider, model,
            llm_api_base or settings.api_default_base,
            llm_api_key or settings.custom_llm_keys.get("deepseek", ""),
        )
        result = json.loads(resp_text)
        return {
            "action": result.get("action", "search"),
            "collection": result.get("collection"),
            "reason": result.get("reason", ""),
        }
    except Exception as e:
        logger.warning("[router] 路由失败，默认检索: %s", e)
        return {"action": "search", "collection": None, "reason": f"路由失败: {e}"}