"""Agent Tools —— 将 Qdrant 操作和文档解析包装为 LLM 可调用的函数。

注册表模式：每个 tool 一条记录（name → {definition, handler, core}）。
新增工具只需加一条记录，TOOLS / CORE_TOOLS / execute_tool 全部自动生成。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient

from app.config import settings

logger = logging.getLogger(__name__)

# ── Agent 系统提示词 ────────────────────────────────────────

HYBRID_RAG_APPENDIX = """\n\n---\n附加能力：你可以使用以下知识库工具搜索本地文档：
- search_knowledge_base: 精确检索文档内容
- aggregate_documents: 跨文档全局汇总分析
- list_documents: 列出知识库中的文件列表

当用户问题涉及本地文件、文档、知识库内容时，优先调用这些工具获取信息。"""


# ── 工具实现 ─────────────────────────────────────────────────


def _tool_search(args: dict) -> str:
    """混合检索，返回文档片段（不含 LLM 生成）。"""
    from app.retriever import get_retriever

    query = args.get("query", "")
    if not query:
        return "错误: query 不能为空"

    filters = args.get("filters")
    documents = get_retriever().retrieve(
        query, settings.qdrant_collection, top_k=10, filters=filters,
    )
    if not documents:
        return f"未找到与 '{query}' 相关的文档。"

    parts: list[str] = []
    for i, doc in enumerate(documents, 1):
        src = doc.get("filename", "?")
        score = doc.get("rerank_score")
        score_str = f" (相关度: {score:.3f})" if score else ""
        parts.append(f"[{i}] {src}{score_str}\n{doc['text'][:800]}")
    return "\n\n".join(parts)


def _tool_list_docs(_args: dict) -> str:
    """列出所有已索引文档。"""
    from app.main import _list_docs_from_qdrant

    docs = _list_docs_from_qdrant()
    if not docs:
        return "知识库中暂无文档。"
    lines = [
        f"- {d['filename']} ({d['chunks']} chunks, source: {d['source']})"
        for d in docs[:30]
    ]
    return f"知识库共 {len(docs)} 个文档:\n" + "\n".join(lines)


def _tool_get_content(args: dict) -> str:
    """获取文档 chunk 原文。"""
    from qdrant_client import QdrantClient
    from app.models import get_embed_model

    filename = args.get("filename", "")
    max_chunks = args.get("max_chunks", 20)
    if not filename:
        return "错误: filename 不能为空"

    try:
        embed_model = get_embed_model()
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        query_vector = embed_model.get_query_embedding(filename)
        results = client.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            using="text-dense",
            limit=max_chunks,
            query_filter={
                "must": [{"key": "filename", "match": {"value": filename}}],
            },
            with_payload=True,
        )
    except Exception as e:
        return f"错误: 检索失败 — {e}"

    if not results.points:
        return f"未找到文档: {filename}"

    parts = [f"文档《{filename}》的内容片段：\n"]
    for i, p in enumerate(results.points, 1):
        text = p.payload.get("text", "") if p.payload else ""
        parts.append(f"[Chunk {i}] {text[:800]}")
    return "\n\n".join(parts)


def _tool_preview(args: dict) -> str:
    """解析 + 分块预览。"""
    from app.parser import parse_file

    file_path = args.get("file_path", "")
    path = Path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 — {file_path}"
    if not path.is_file():
        return f"错误: 不是文件 — {file_path}"

    parsed = parse_file(path)

    from llama_index.core import Document as LlamaDocument
    from app.indexer import _create_splitter

    doc = LlamaDocument(text=parsed.text, metadata={"source": str(path)})
    splitter = _create_splitter(settings.chunk_size, settings.chunk_overlap, settings.chunk_method)
    nodes = splitter.get_nodes_from_documents([doc])

    samples = "\n".join(
        f"  [{i}] {n.text[:120]}..." for i, n in enumerate(nodes[:3], 1)
    )
    return (
        f"文件: {path.name}\n"
        f"格式: {parsed.source_format}\n"
        f"解析器: {parsed.source_format}\n"
        f"总字符数: {len(parsed.text)}\n"
        f"页数: {parsed.page_count or 'N/A'}\n"
        f"分块策略: {settings.chunk_method} (size={settings.chunk_size}, overlap={settings.chunk_overlap})\n"
        f"分块数: {len(nodes)}\n"
        f"元数据: {parsed.metadata}\n"
        f"前3块采样:\n{samples if samples else '  (无内容)'}"
    )


def _tool_index_file(args: dict) -> str:
    """索引单个文件。"""
    from app.indexer import DocumentIndexer

    file_path = args.get("file_path", "")
    replace = args.get("replace", False)
    path = Path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 — {file_path}"

    indexer = DocumentIndexer()
    try:
        chunks = indexer.index_file(path, replace=replace)
        from app.main import build_doc_list_cache
        import threading
        threading.Thread(target=build_doc_list_cache, daemon=True).start()
        return f"✓ 已索引: {path.name}，{chunks} 个 chunk。该文件现在可以被检索了。"
    except ValueError as e:
        return f"跳过: {path.name} — {e}"
    except Exception as e:
        return f"错误: 索引 {path.name} 失败 — {e}"


def _tool_index_dir(args: dict) -> str:
    """批量索引目录。"""
    import uuid
    from app.indexer import DocumentIndexer, _set_task_status

    directory = args.get("directory", "")
    replace = args.get("replace", False)
    async_mode = args.get("async_mode", False)
    dir_path = Path(directory)

    if not dir_path.exists() or not dir_path.is_dir():
        return f"错误: 目录不存在 — {directory}"

    from app.parser import collect_files
    files = collect_files(dir_path, exclude_dirs=settings.exclude_dir_list)
    if not files:
        return f"目录为空或无支持的文件: {directory}"

    if async_mode:
        task_id = uuid.uuid4().hex[:12]
        _set_task_status(task_id, status="pending")
        import threading
        indexer = DocumentIndexer()
        t = threading.Thread(
            target=indexer.index_directory,
            args=(dir_path, True, replace, False, None, task_id),
            daemon=True,
        )
        t.start()
        return (
            f"✓ 已启动异步索引: {directory}\n"
            f"文件数: {len(files)}\n"
            f"task_id: {task_id}\n"
            f"可随时调 get_index_status 查进度。"
        )

    indexer = DocumentIndexer()
    result = indexer.index_directory(dir_path, replace=replace)
    from app.main import build_doc_list_cache
    import threading
    threading.Thread(target=build_doc_list_cache, daemon=True).start()
    return (
        f"✓ 索引完成: {directory}\n"
        f"文件数: {result.total_files}, 成功: {result.indexed}, "
        f"跳过: {result.skipped}, 失败: {result.failed}, "
        f"chunks: {result.total_chunks}"
    )


def _tool_status(args: dict) -> str:
    """查询异步索引进度。"""
    from app.indexer import get_task_status

    task_id = args.get("task_id", "")
    status = get_task_status(task_id)
    if status is None:
        return f"错误: 任务不存在 — {task_id}"
    return (
        f"任务: {task_id}\n"
        f"状态: {status.get('status', '?')}\n"
        f"进度: {status.get('progress', '?')}\n"
        f"当前文件: {status.get('current_file', '')}\n"
        f"成功/失败/跳过: {status.get('indexed',0)}/{status.get('failed',0)}/{status.get('skipped',0)}"
    )


def _tool_delete(args: dict) -> str:
    """删除文档索引。"""
    from app.indexer import DocumentIndexer

    source = args.get("source", "")
    if not source:
        return "错误: source 不能为空"

    indexer = DocumentIndexer()
    indexer._collection_override = settings.qdrant_collection
    indexer._delete_source(source)
    indexer._delete_sparse_source(source)
    from app.main import build_doc_list_cache
    import threading
    threading.Thread(target=build_doc_list_cache, daemon=True).start()
    return f"✓ 已从知识库删除: {source}"


def _tool_aggregate(args: dict) -> str:
    """跨文档聚合搜索（Agent 模式下不调 LLM，只返回文档）。"""
    from app.retriever import get_retriever

    query = args.get("query", "")
    filters = args.get("filters")
    retriever = get_retriever()

    all_docs: list[dict] = []
    seen: set[str] = set()

    queries = [query, f"汇总 {query}", f"{query} 统计"][:2]

    for q in queries:
        try:
            docs = retriever.retrieve(
                q, settings.qdrant_collection, top_k=10, filters=filters,
            )
        except Exception:
            continue
        for doc in docs:
            key = doc["text"][:200]
            if key not in seen:
                seen.add(key)
                all_docs.append(doc)

    all_docs.sort(
        key=lambda x: x.get("rerank_score", x.get("score", 0)),
        reverse=True,
    )
    top = all_docs[:25]

    if not top:
        return f"未找到与 '{query}' 相关的内容。"

    parts: list[str] = [f"共检索到 {len(top)} 条相关内容（去重后）：\n"]
    for i, doc in enumerate(top, 1):
        src = doc.get("filename", "?")
        score = doc.get("rerank_score")
        score_str = f" (相关度: {score:.3f})" if score else ""
        parts.append(f"[{i}] {src}{score_str}\n{doc['text'][:800]}")
    return "\n\n".join(parts)


def _tool_list_collections(_args: dict) -> str:
    """列出所有 Qdrant collections。"""
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    try:
        cols = client.get_collections().collections
        names = sorted(
            c.name for c in cols if not c.name.endswith("_sparse")
        )
        if not names:
            return "没有找到任何 collection。"
        return "Qdrant 知识库集合:\n" + "\n".join(f"  - {n}" for n in names)
    except Exception as e:
        return f"错误: 无法连接 Qdrant — {e}"


# ── 注册表 ────────────────────────────────────────────────────

# 每条记录: {name: {definition, handler, core}}
# core=True → 会被 _ensure_rag_tools() 自动补齐到 tools 列表

_registry: dict[str, dict[str, Any]] = {
    "search_knowledge_base": {
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "search_knowledge_base",
                "description": "在知识库中检索文档并回答问题。走 Dense+Sparse 双路召回 + RRF 融合 + Cross-Encoder 重排。适合：文档问答、内容查询、事实检索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询，用自然语言或关键词。越具体越好。"},
                        "filters": {"type": "object", "description": "可选 metadata 过滤条件，如 {\"source_format\": \"pdf\"}。支持 * 通配符。"},
                    },
                    "required": ["query"],
                },
            },
        },
        "handler": _tool_search,
    },
    "aggregate_documents": {
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "aggregate_documents",
                "description": "跨文档全局分析——多轮检索 + 汇总去重 + LLM 整体统计分析。适合：'总结知识库趋势'、'跨文档统计'。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "分析主题或问题"},
                        "filters": {"type": "object", "description": "可选过滤条件"},
                    },
                    "required": ["query"],
                },
            },
        },
        "handler": _tool_aggregate,
    },
    "list_documents": {
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "list_documents",
                "description": "列出知识库中所有已索引的文档，显示文件名、路径和 chunk 数量。适合：用户问'有哪些文件'、'知识库有什么'时用。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _tool_list_docs,
    },
    "get_document_content": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "get_document_content",
                "description": "获取知识库中某个文档的完整文本内容（按 chunk 返回）。适合：用户要求'给我看某文档的完整内容'时用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文档文件名（精确匹配），如 'report.pdf'"},
                        "max_chunks": {"type": "integer", "description": "最多返回多少 chunk，默认 20"},
                    },
                    "required": ["filename"],
                },
            },
        },
        "handler": _tool_get_content,
    },
    "preview_document": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "preview_document",
                "description": "预览一个文件的解析结果——不写入知识库。返回文件类型、chunk 数量、采样内容和元数据。适合：导入前检查文件质量。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "服务器上的文件绝对路径，如 /app/data/uploads/report.pdf"},
                    },
                    "required": ["file_path"],
                },
            },
        },
        "handler": _tool_preview,
    },
    "index_file": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "index_file",
                "description": "索引单个文件到知识库。文件会被解析、分块、生成 Dense+Sparse 双向量后写入 Qdrant。索引完成后该文件即可被检索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "服务器上的文件绝对路径，如 /app/data/uploads/report.pdf"},
                        "replace": {"type": "boolean", "description": "如果该文件已索引，是否先删除旧数据再重新索引。默认 false。"},
                    },
                    "required": ["file_path"],
                },
            },
        },
        "handler": _tool_index_file,
    },
    "index_directory": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "index_directory",
                "description": "批量索引整个目录中的所有支持文件（PDF/Word/Excel/PPT/Markdown/TXT），递归扫描子目录。大目录可能需要几分钟。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "服务器上的目录绝对路径，如 /app/data/uploads/"},
                        "replace": {"type": "boolean", "description": "是否强制重建所有索引。默认 false（追加模式）。"},
                        "async_mode": {"type": "boolean", "description": "是否异步执行。true=立即返回 task_id，false=等待完成。大目录推荐用 true。"},
                    },
                    "required": ["directory"],
                },
            },
        },
        "handler": _tool_index_dir,
    },
    "get_index_status": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "get_index_status",
                "description": "查询异步索引任务的进度。返回状态（pending/running/done）、进度（15/50）、当前处理文件等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "异步索引发起时返回的 task_id"},
                    },
                    "required": ["task_id"],
                },
            },
        },
        "handler": _tool_status,
    },
    "delete_document": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "delete_document",
                "description": "从知识库中删除一个文档的索引。不会删除磁盘上的原始文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "文档在 Qdrant 中的 source 路径（完整路径）。可先调 list_documents 获取。"},
                    },
                    "required": ["source"],
                },
            },
        },
        "handler": _tool_delete,
    },
    "list_collections": {
        "core": False,
        "definition": {
            "type": "function",
            "function": {
                "name": "list_collections",
                "description": "列出 Qdrant 向量数据库中的所有知识库集合（collection）。每个 collection 是一个独立的文档库。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _tool_list_collections,
    },
}


# ── 自动推导的公共接口 ──────────────────────────────────────

TOOLS: list[dict] = [t["definition"] for t in _registry.values()]

CORE_TOOLS: list[dict] = [
    t["definition"] for t in _registry.values() if t["core"]
]

_AGENT_TOOL_NAMES: set[str] = set(_registry.keys())
_CORE_TOOL_NAMES: set[str] = {k for k, t in _registry.items() if t["core"]}


def execute_tool(name: str, arguments: dict) -> str:
    """执行一个 tool 调用，返回结果字符串给 LLM。

    根据注册表自动分发，不再需要手动 if/elif 链。
    """
    logger.info("Agent tool: %s(%s)", name, arguments)
    tool = _registry.get(name)
    if not tool:
        return f"错误: 未知工具 {name}"
    try:
        return tool["handler"](arguments)
    except Exception as e:
        logger.exception("Tool %s 执行失败", name)
        return f"错误: {name} 执行失败 — {e}"


def is_rag_tool(name: str) -> bool:
    """判断工具名是否属于 RAG 内部工具（用于区分外部工具）。"""
    return name in _AGENT_TOOL_NAMES
