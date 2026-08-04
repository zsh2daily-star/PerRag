"""Agent Tools —— 将 Qdrant 操作和文档解析包装为 LLM 可调用的函数。

注册表模式：每个 tool 一条记录（name → {definition, handler, core}）。
新增工具只需加一条记录，TOOLS / CORE_TOOLS / execute_tool 全部自动生成。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from app.config import settings

logger = logging.getLogger(__name__)

# ── Agent 系统提示词 ────────────────────────────────────────

HYBRID_RAG_APPENDIX = """\n\n---\n你还可以使用以下本地知识库工具：

{collections_note}

- search_knowledge_base: 在知识库中搜索文档内容
- aggregate_documents: 跨文档全局汇总分析
- list_documents: 查看知识库中有哪些文件
- index_file: 将单个文件导入知识库（支持 PDF/Word/Excel/PPT/Markdown/TXT）
- index_directory: 批量导入整个目录
- preview_document: 快速查看文件信息（不会重复解析）
- delete_document: 从知识库中删除文件索引
- list_collections: 查看有哪些知识库集合

当用户提到文件导入、索引、检索、知识库管理时，优先考虑这些工具。"""


def _get_collections_hint() -> str:
    """动态生成可用 collection 列表提示（从 Qdrant 实时获取）。"""
    try:
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        cols = client.get_collections().collections
        dense_cols = sorted(
            c.name for c in cols if not c.name.endswith("_sparse")
        )
        if not dense_cols:
            return "（暂无可用知识库集合）"
        return "当前可用的知识库集合:\n  - " + "\n  - ".join(dense_cols)
    except Exception:
        return ""


# ── 宿主机→容器路径转换 ───────────────────────────────────

# Docker 挂载映射：宿主机路径前缀 → 容器内路径前缀
# 用户对话中说宿主机路径，自动转成容器内路径
_HOST_TO_CONTAINER_PATHS: dict[str, str] = {
    "/mnt/个人文件": "/app/data/personal",
    "/mnt/公司文件/公司内部文件": "/app/data/ragtemp",
}


def _to_container_path(path_str: str) -> str:
    """将用户可能输入的宿主机路径转为容器内实际路径。

    用户在对话中说的是宿主机路径（如 /mnt/个人文件/中医/xxx.pdf），
    但工具执行时需要容器内路径（/app/data/personal/中医/xxx.pdf）。
    如果路径已经是容器内路径则不转换。
    """
    for host_prefix, container_prefix in _HOST_TO_CONTAINER_PATHS.items():
        if path_str.startswith(host_prefix):
            return path_str.replace(host_prefix, container_prefix, 1)
    return path_str


# ── 工具实现 ─────────────────────────────────────────────────


def _tool_search(args: dict) -> str:
    """混合检索，返回文档片段（不含 LLM 生成）。"""
    from app.retriever import get_retriever

    query = args.get("query", "")
    if not query:
        return "错误: query 不能为空"

    collection = args.get("collection") or settings.qdrant_collection
    filters = args.get("filters")
    documents = get_retriever().retrieve(
        query, collection, top_k=10, filters=filters,
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


def _tool_list_docs(args: dict) -> str:
    """列出所有已索引文档（可按 collection 筛选）。"""
    from app.main import _list_docs_from_qdrant

    collection = args.get("collection") or settings.qdrant_collection
    docs = _list_docs_from_qdrant(collection=collection)
    if not docs:
        return f"知识库 {collection} 中暂无文档。"
    lines = [
        f"- {d['filename']} ({d['chunks']} chunks, source: {d['source']})"
        for d in docs[:30]
    ]
    return f"知识库 {collection} 共 {len(docs)} 个文档:\n" + "\n".join(lines)


def _tool_get_content(args: dict) -> str:
    """获取文档 chunk 原文。"""
    from qdrant_client import QdrantClient
    from app.models import get_embed_model

    filename = args.get("filename", "")
    max_chunks = args.get("max_chunks", 20)
    collection = args.get("collection") or settings.qdrant_collection
    if not filename:
        return "错误: filename 不能为空"

    try:
        embed_model = get_embed_model()
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        query_vector = embed_model.get_query_embedding(filename)
        results = client.query_points(
            collection_name=collection,
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
    """快速预览文件基本信息——不跑完整解析，避免对大 PDF 耗时过长。"""
    file_path = _to_container_path(args.get("file_path", ""))
    path = Path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 — {file_path}"
    if not path.is_file():
        return f"错误: 不是文件 — {file_path}"

    size_kb = path.stat().st_size // 1024
    suffix = path.suffix.lower()

    lines = [
        f"文件: {path.name}",
        f"大小: {size_kb:,} KB ({size_kb / 1024:.1f} MB)" if size_kb > 1024 else f"大小: {size_kb:,} KB",
        f"格式: {suffix}",
    ]

    # PDF：快速获取页数 + 文本层探测
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            lines.append(f"页数: {len(reader.pages)}")

            # 只采样前 3 页，每页最多 500 字
            samples: list[str] = []
            for page in reader.pages[:3]:
                text = (page.extract_text() or "").strip()
                if text:
                    samples.append(text[:500])
            if samples:
                lines.append(f"文字层: ✅ 可提取（前3页采样 {sum(len(s) for s in samples)} 字）")
                lines.append("采样内容:")
                for i, s in enumerate(samples, 1):
                    lines.append(f"  [第{i}页] {s[:200]}...")
            else:
                lines.append("文字层: ❌ 无 → 可能是扫描件，需 MinerU OCR 才能解析")
        except Exception as e:
            lines.append(f"PDF 预览失败: {e}")

    # 纯文本：直接采样前 1000 字
    elif suffix in {".txt", ".md", ".markdown"}:
        try:
            text = path.read_text()[:1000]
            lines.append(f"前 1000 字采样:\n  {text}")
        except Exception as e:
            lines.append(f"读取失败: {e}")

    # 其他格式给基本文件信息就够了
    else:
        lines.append("提示: 使用 index_file / index_directory 导入后可检索内容")

    return "\n".join(lines)


def _tool_index_file(args: dict) -> str:
    """索引单个文件。"""
    from app.indexer import DocumentIndexer

    file_path = _to_container_path(args.get("file_path", ""))
    replace = args.get("replace", False)
    collection = args.get("collection") or settings.qdrant_collection
    path = Path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 — {file_path}"

    indexer = DocumentIndexer()
    indexer._collection_override = collection
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

    directory = _to_container_path(args.get("directory", ""))
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
        collection = args.get("collection") or settings.qdrant_collection
        indexer = DocumentIndexer()
        t = threading.Thread(
            target=indexer.index_directory,
            args=(dir_path, True, replace, False, collection, task_id),
            daemon=True,
        )
        t.start()
        return (
            f"✓ 已启动异步索引: {directory}\n"
            f"文件数: {len(files)}\n"
            f"task_id: {task_id}\n"
            f"可随时调 get_index_status 查进度。"
        )

    collection2 = args.get("collection") or settings.qdrant_collection
    indexer2 = DocumentIndexer()
    result = indexer2.index_directory(dir_path, replace=replace, collection=collection2)
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
    """删除文档索引（按文件名精确匹配，仅展示信息，不执行删除）。"""
    collection = args.get("collection") or settings.qdrant_collection
    filename = args.get("filename", "")
    if not filename:
        return "错误: filename 不能为空"

    docs = _list_docs_qdrant(collection, filename)
    if not docs:
        return f"未找到文件「{filename}」，知识库 {collection} 中不存在此文件。"

    doc = docs[0]
    return (
        f"文件「{filename}」在知识库 {collection} 中：\n"
        f"  chunk 数: {doc['chunks']}\n"
        f"  来源路径: {doc['source']}\n\n"
        f"如需删除，请通过以下命令操作：\n"
        f"curl -X DELETE http://rag-api:8000/index/document \\\n"
        f"  -H \"Content-Type: application/json\" \\\n"
        f"  -d '{{\"filename\": \"{filename}\", \"collection\": \"{collection}\"}}'"
    )


def _list_docs_qdrant(collection: str, filename: str) -> list[dict]:
    """按文件名精确查询文档是否存在及 chunk 数。"""
    from qdrant_client import QdrantClient
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    existing = {c.name for c in client.get_collections().collections}
    if collection not in existing:
        return []
    count = client.count(
        collection_name=collection,
        count_filter={"must": [{"key": "filename", "match": {"value": filename}}]},
        exact=True,
    )
    if count.count == 0:
        return []
    # 拿一条看 source
    points = client.scroll(
        collection_name=collection, limit=1,
        with_payload=["source"],
        scroll_filter={"must": [{"key": "filename", "match": {"value": filename}}]},
    )[0]
    source = points[0].payload.get("source", "") if points else ""
    return [{"filename": filename, "chunks": count.count, "source": source}]


def _tool_aggregate(args: dict) -> str:
    """跨文档聚合搜索（Agent 模式下不调 LLM，只返回文档）。"""
    from app.retriever import get_retriever

    query = args.get("query", "")
    collection = args.get("collection") or settings.qdrant_collection
    filters = args.get("filters")
    retriever = get_retriever()

    all_docs: list[dict] = []
    seen: set[str] = set()

    queries = [query, f"汇总 {query}", f"{query} 统计"][:2]

    for q in queries:
        try:
            docs = retriever.retrieve(
                q, collection, top_k=10, filters=filters,
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
                "description": "在指定知识库集合中检索文档。支持 Dense+Sparse 双路召回 + RRF 融合 + Cross-Encoder 重排。适合：文档问答、内容查询、事实检索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询，用自然语言或关键词。越具体越好。"},
                        "collection": {"type": "string", "description": "目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。"},
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
                "description": "跨文档全局分析——多轮检索 + 汇总去重。适合：'总结知识库趋势'、'跨文档统计'。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "分析主题或问题"},
                        "collection": {"type": "string", "description": "目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。"},
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
                "description": "列出指定知识库集合中所有已索引的文档，显示文件名和 chunk 数量。适合：用户问'有哪些文件'、'知识库有什么'时用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。"},
                    },
                },
            },
        },
        "handler": _tool_list_docs,
    },
    "get_document_content": {
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "get_document_content",
                "description": "获取知识库中某个文档的完整文本内容（按 chunk 返回）。适合：用户要求'给我看某文档的完整内容'时用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文档文件名（精确匹配），如 'report.pdf'"},
                        "collection": {"type": "string", "description": "目标知识库集合名称。不传则使用默认集合。"},
                        "max_chunks": {"type": "integer", "description": "最多返回多少 chunk，默认 20"},
                    },
                    "required": ["filename"],
                },
            },
        },
        "handler": _tool_get_content,
    },
    "preview_document": {
        "core": True,
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
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "index_file",
                "description": "将单个文件导入知识库（解析→分块→向量写入 Qdrant）。务必指定 collection 参数来导入到正确的知识库。支持 PDF/Word/Excel/PPT/Markdown/TXT。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "文件绝对路径"},
                        "collection": {"type": "string", "description": "目标知识库集合，如 '中医' 或 'workfile'。务必填写！"},
                        "replace": {"type": "boolean", "description": "是否先删除旧索引再重建。默认 false。"},
                    },
                    "required": ["file_path"],
                },
            },
        },
        "handler": _tool_index_file,
    },
    "index_directory": {
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "index_directory",
                "description": "批量索引目录中的所有文件。务必指定 collection 参数来导入到正确的知识库。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "目录绝对路径"},
                        "collection": {"type": "string", "description": "目标知识库集合，如 '中医' 或 'workfile'。务必填写！"},
                        "replace": {"type": "boolean", "description": "是否强制重建。默认 false。"},
                        "async_mode": {"type": "boolean", "description": "异步执行返回 task_id。大目录推荐 true。"},
                    },
                    "required": ["directory"],
                },
            },
        },
        "handler": _tool_index_dir,
    },
    "get_index_status": {
        "core": True,
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
        "core": True,
        "definition": {
            "type": "function",
            "function": {
                "name": "delete_document",
                "description": "查看知识库中文档的详细信息并获取手动删除命令。此工具不执行删除，只返回文件信息和 curl 删除命令，让用户自行决定是否在终端执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文档文件名（精确匹配），如 'report.pdf'"},
                        "collection": {"type": "string", "description": "目标知识库集合名称"},
                    },
                    "required": ["filename"],
                },
            },
        },
        "handler": _tool_delete,
    },
    "list_collections": {
        "core": True,
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
