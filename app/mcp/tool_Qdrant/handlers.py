"""Agent Tools —— 将 RAG Core 能力包装为 LLM 可调用的函数。

注册表模式：每个 tool 一条记录（name → {definition, handler, core}），
供 MCP server 直接复用 handler。

所有检索/索引/Qdrant 操作统一转发到 app.core_Qdrant 公开契约（阶段 A3），
本模块只负责：参数解析、宿主机→容器路径转换、结果文本格式化。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

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
    from app.core_Qdrant import retrieve

    query = args.get("query", "")
    if not query:
        return "错误: query 不能为空"

    collection = args.get("collection") or settings.qdrant_collection
    filters = args.get("filters")
    documents = retrieve(query, collection, top_k=10, filters=filters)
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
    from app.core_Qdrant import docs_meta

    collection = args.get("collection") or settings.qdrant_collection
    docs = docs_meta(collection)
    if not docs:
        return f"知识库 {collection} 中暂无文档。"
    lines = [
        f"- {d['filename']} ({d['chunks']} chunks, source: {d['source']})"
        for d in docs[:30]
    ]
    return f"知识库 {collection} 共 {len(docs)} 个文档:\n" + "\n".join(lines)


def _tool_get_content(args: dict) -> str:
    """获取文档 chunk 原文。"""
    from app.core_Qdrant import get_document_chunks

    filename = args.get("filename", "")
    max_chunks = args.get("max_chunks", 20)
    collection = args.get("collection") or settings.qdrant_collection
    if not filename:
        return "错误: filename 不能为空"

    try:
        chunks = get_document_chunks(filename, collection, max_chunks)
    except Exception as e:
        return f"错误: 检索失败 — {e}"

    if not chunks:
        return f"未找到文档: {filename}"

    parts = [f"文档《{filename}》的内容片段：\n"]
    for i, text in enumerate(chunks, 1):
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
    import threading

    from app.core_Qdrant import document_meta, index_file, refresh_docs_meta_cache

    file_path = _to_container_path(args.get("file_path", ""))
    replace = args.get("replace", False)
    collection = args.get("collection") or settings.qdrant_collection
    path = Path(file_path)
    if not path.exists():
        return f"错误: 文件不存在 — {file_path}"

    # 排除检查：与 index_directory 保持一致
    for ex in settings.exclude_path_list:
        ex_path = Path(ex)
        if not ex_path.is_absolute():
            # 相对路径：匹配文件名或父目录名
            if ex_path.name == path.name or ex_path.name in [p.name for p in path.parents]:
                return f"跳过: {path.name} — 在排除目录 ({ex}) 中"
        else:
            try:
                if path.resolve().is_relative_to(ex_path.resolve()):
                    return f"跳过: {path.name} — 在排除目录 ({ex}) 中"
            except (ValueError, OSError):
                pass

    # 重复检查：避免重复导入大文件
    if not replace and document_meta(path.name, collection):
        return (
            f"提示: {path.name} 已在知识库 {collection} 中。"
            f"如需强制重建请设置 replace=true。"
        )

    try:
        chunks = index_file(path, replace=replace, collection=collection)
        threading.Thread(target=refresh_docs_meta_cache, daemon=True).start()
        return f"✓ 已索引: {path.name}，{chunks} 个 chunk。该文件现在可以被检索了。"
    except ValueError as e:
        return f"跳过: {path.name} — {e}"
    except Exception as e:
        return f"错误: 索引 {path.name} 失败 — {e}"


def _tool_index_dir(args: dict) -> str:
    """批量索引目录。"""
    import threading
    import uuid

    from app.core_Qdrant import index_directory, refresh_docs_meta_cache, set_task_status

    directory = _to_container_path(args.get("directory", ""))
    replace = args.get("replace", False)
    async_mode = args.get("async_mode", False)
    dir_path = Path(directory)

    if not dir_path.exists() or not dir_path.is_dir():
        return f"错误: 目录不存在 — {directory}"

    from app.core_Qdrant.parser import collect_files
    files = collect_files(dir_path, exclude_paths=settings.exclude_path_list)
    if not files:
        return f"目录为空或无支持的文件: {directory}"

    collection = args.get("collection") or settings.qdrant_collection

    if async_mode:
        task_id = uuid.uuid4().hex[:12]
        set_task_status(task_id, status="pending")
        t = threading.Thread(
            target=index_directory,
            kwargs={
                "directory": dir_path,
                "recursive": True,
                "replace": replace,
                "skip_existing": False,
                "collection": collection,
                "task_id": task_id,
            },
            daemon=True,
        )
        t.start()
        return (
            f"✓ 已启动异步索引: {directory}\n"
            f"文件数: {len(files)}\n"
            f"task_id: {task_id}\n"
            f"可随时调 get_index_status 查进度。"
        )

    result = index_directory(dir_path, replace=replace, collection=collection)
    threading.Thread(target=refresh_docs_meta_cache, daemon=True).start()
    return (
        f"✓ 索引完成: {directory}\n"
        f"文件数: {result.total_files}, 成功: {result.indexed}, "
        f"跳过: {result.skipped}, 失败: {result.failed}, "
        f"chunks: {result.total_chunks}"
    )


def _tool_status(args: dict) -> str:
    """查询异步索引进度。"""
    from app.core_Qdrant import get_index_status

    task_id = args.get("task_id", "")
    status = get_index_status(task_id)
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
    """查询文档索引信息（按文件名精确匹配，只读；删除请用 qdrant_doc_delete 工具）。"""
    from app.core_Qdrant import document_meta

    collection = args.get("collection") or settings.qdrant_collection
    filename = args.get("filename", "")
    if not filename:
        return "错误: filename 不能为空"

    doc = document_meta(filename, collection)
    if not doc:
        return f"未找到文件「{filename}」，知识库 {collection} 中不存在此文件。"

    return (
        f"文件「{filename}」在知识库 {collection} 中：\n"
        f"  chunk 数: {doc['chunks']}\n"
        f"  来源路径: {doc['source']}\n\n"
        f"如需删除该文档的全部索引（不可恢复，不删磁盘原文件），"
        f"请调用 qdrant_doc_delete 工具：先以 confirm=false 预演，确认无误后再以 confirm=true 执行。"
    )


def _tool_doc_delete(args: dict) -> str:
    """删除文档索引（dense + sparse 两路向量），两阶段确认：confirm 默认 false。"""
    import threading

    from app.core_Qdrant import (
        delete_document,
        document_meta,
        refresh_docs_meta_cache,
    )

    collection = args.get("collection") or settings.qdrant_collection
    filename = args.get("filename", "")
    if not filename:
        return "错误: filename 不能为空"
    confirm = bool(args.get("confirm", False))

    doc = document_meta(filename, collection)
    if not doc:
        return f"未找到文件「{filename}」，知识库 {collection} 中不存在此文件，无需删除。"

    if not confirm:
        return (
            f"⚠ 待删除预演（尚未执行任何操作）：\n"
            f"  文件: {filename}\n"
            f"  知识库: {collection}\n"
            f"  chunk 数: {doc['chunks']}\n"
            f"  来源路径: {doc['source']}\n\n"
            f"执行后将从 dense + sparse 两路向量索引中移除该文件的全部片段，"
            f"操作不可恢复（磁盘上的原始文件不会被删）。\n"
            f"请向用户确认；用户明确同意后，带 confirm=true 重新调用本工具执行删除。"
        )

    delete_document(filename, collection=collection)
    threading.Thread(target=refresh_docs_meta_cache, daemon=True).start()
    return (
        f"✓ 已删除: {filename}（知识库 {collection}，{doc['chunks']} 个 chunk）。\n"
        f"该文件已无法被检索；磁盘原文件保留。"
    )


def _tool_aggregate(args: dict) -> str:
    """跨文档聚合检索（Agent 模式下不调 LLM，只返回文档）。

    复用 rag_core.Retriever.aggregate_docs —— 与 HTTP /aggregate
    共用同一套多集合 × 多变体召回去重逻辑（aggregate_top_k，默认 30），
    区别仅在于不调 LLM，文档交回外部 Agent 自行分析。
    """
    from app.core_Qdrant import get_retriever

    query = (args.get("query") or "").strip()
    if not query:
        return "错误: query 不能为空"

    collection = args.get("collection") or settings.qdrant_collection
    collections = args.get("collections")
    filters = args.get("filters")

    top = get_retriever().aggregate_docs(
        query,
        collection=collection,
        collections=collections,
        filters=filters,
    )

    if not top:
        return f"未找到与 '{query}' 相关的内容。"

    col_count = len(collections) if collections else 1
    parts: list[str] = [
        f"共聚合 {col_count} 个知识库，多轮检索去重后取相关度最高的 {len(top)} 条内容：\n"
    ]
    for i, doc in enumerate(top, 1):
        src = doc.get("filename", "?")
        score = doc.get("rerank_score")
        score_str = f" (相关度: {score:.3f})" if score else ""
        parts.append(f"[{i}] {src}{score_str}\n{doc['text'][:800]}")
    return "\n\n".join(parts)


def _tool_list_collections(_args: dict) -> str:
    """列出所有 Qdrant collections。"""
    from app.core_Qdrant import list_collections

    try:
        names = list_collections()
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
                        "collections": {"type": "array", "items": {"type": "string"}, "description": "多个目标知识库集合名称，跨知识库聚合时使用；传入后优先于 collection。"},
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
                "description": "查看知识库中文档的详细信息（chunk 数、来源路径）。此工具只读不删除；确认删除请用 qdrant_doc_delete（先 confirm=false 预演、再 confirm=true 执行）。",
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
