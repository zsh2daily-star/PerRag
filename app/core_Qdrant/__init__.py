"""Qdrant 基础能力层 —— 文档解析 → 分块 → 向量化 → 检索 的完整技术管线。

纯技术能力、零业务语义（不知"企业/材料"为何物）；
业务层（FastAPI 路由 / MCP 工具 / CLI）只面向本包的公开契约，
不直接触碰 Qdrant 客户端、模型单例或分块器等内部实现：

检索:
    retrieve(query, collection=None, top_k=None, filters=None) -> list[dict]

索引:
    index_file(path, replace=False, skip_existing=False, collection=None, material_id=None) -> int
    index_directory(directory=None, recursive=True, replace=False,
                    skip_existing=False, collection=None, task_id=None) -> DirectoryIndexResult
    delete_document(filename, collection=None) -> None
    get_index_status(task_id) -> dict | None

文档元数据:
    docs_meta(collection=None) -> list[dict]
    get_docs_meta_cached(force_refresh=False) -> list[dict]
    refresh_docs_meta_cache(collection=None) -> list[dict]
    document_meta(filename, collection=None) -> dict | None
    get_document_chunks(filename, collection=None, max_chunks=20) -> list[str]
    list_collections() -> list[str]

可替换点配置:
    RAG_CORE_LOAD_CONFIG  —— 分块参数 / 模型名 / 召回重排数量 / RRF 权重（见 config.py）

LLM 是边界外可插拔能力（问答生成由外部 Agent 承担；_call_llm 仅供可开关的 HyDE 检索优化）。

模块组成:
    config.py        RAG_CORE_LOAD_CONFIG（分块/模型/RRF 可替换点）
    parser.py        多格式文档解析（PDF/Word/Excel/PPT/Markdown/TXT）
    mineru_client.py MinerU PDF 解析 HTTP 客户端（GPU OCR）
    indexer.py       索引器（解析→分块→双向量写入 Qdrant）
    retriever.py     混合检索器（双路召回→RRF→重排）
    models.py        BGE-M3 / BGE-Reranker 模型单例
    docs_meta.py     文档/集合元数据查询与缓存
"""

from pathlib import Path

from app.core_Qdrant.config import RAG_CORE_LOAD_CONFIG, RagCoreConfig
from app.core_Qdrant.docs_meta import (
    delete_document,
    docs_meta,
    document_meta,
    get_docs_meta_cached,
    get_document_chunks,
    get_evidence,
    list_collections,
    refresh_docs_meta_cache,
)
from app.core_Qdrant.indexer import (
    DirectoryIndexResult,
    DocumentIndexer,
    FileIndexResult,
    create_splitter,
    get_indexer,
    get_task_status,
    set_task_status,
)
from app.core_Qdrant.models import (
    get_embed_model,
    get_reranker,
    get_sparse_model,
    offload_models_to_cpu,
    reload_models_to_gpu,
    warmup_models,
)
from app.core_Qdrant.retriever import Retriever, get_retriever

__all__ = [
    # ── 配置（A4 可替换点）──
    "RAG_CORE_LOAD_CONFIG",
    "RagCoreConfig",
    # ── 检索契约 ──
    "retrieve",
    "Retriever",
    "get_retriever",
    # ── 索引契约 ──
    "index_file",
    "index_directory",
    "delete_document",
    "get_index_status",
    "set_task_status",
    "DocumentIndexer",
    "DirectoryIndexResult",
    "FileIndexResult",
    "get_indexer",
    "create_splitter",
    # ── 文档元数据契约 ──
    "docs_meta",
    "get_docs_meta_cached",
    "refresh_docs_meta_cache",
    "document_meta",
    "get_document_chunks",
    "get_evidence",
    "list_collections",
    # ── 模型生命周期（供启动预热 / 显存调度复用）──
    "warmup_models",
    "offload_models_to_cpu",
    "reload_models_to_gpu",
    "get_embed_model",
    "get_sparse_model",
    "get_reranker",
]


# ── 检索 ──────────────────────────────────────────────────


def retrieve(
    query: str,
    collection: str | None = None,
    top_k: int | None = None,
    filters: dict[str, str] | None = None,
    hyde: bool | None = None,
) -> list[dict]:
    """混合检索：Dense + Sparse → RRF 融合 → Cross-Encoder 重排。

    参数:
        query: 检索查询
        collection: 目标 Qdrant collection，None=默认 collection
        top_k: 返回数量，None=RAG_CORE_LOAD_CONFIG.rerank_top_k（默认 5）
        filters: 可选 payload 过滤条件，支持 * 通配符
        hyde: 是否启用 HyDE；None=跟随配置，False=关闭（精确匹配提速）
    """
    col = collection or RAG_CORE_LOAD_CONFIG.qdrant_collection
    return get_retriever().retrieve(query, col, top_k=top_k, filters=filters, hyde=hyde)


# ── 索引 ──────────────────────────────────────────────────


def index_file(
    path: str | Path,
    replace: bool = False,
    skip_existing: bool = False,
    collection: str | None = None,
    material_id: str | None = None,
) -> int:
    """索引单个文件：解析 → 分块 → Dense + Sparse 双向量写入 Qdrant。

    返回该文件产生的 chunk 数量（skip_existing 命中返回 0）。

    参数:
        material_id: 业务层材料 ID（阶段 C 由 PostgreSQL 传入覆盖 hash 默认值）
    """
    indexer = get_indexer()
    indexer._collection_override = collection
    return indexer.index_file(
        Path(path), replace=replace, skip_existing=skip_existing, material_id=material_id,
    )


def index_directory(
    directory: str | Path | None = None,
    recursive: bool = True,
    replace: bool = False,
    skip_existing: bool = False,
    collection: str | None = None,
    task_id: str | None = None,
) -> DirectoryIndexResult:
    """批量索引目录中的所有支持文件（同步执行，异步场景由调用方放后台线程）。"""
    return get_indexer().index_directory(
        directory=Path(directory) if directory is not None else None,
        recursive=recursive,
        replace=replace,
        skip_existing=skip_existing,
        collection=collection,
        task_id=task_id,
    )


def get_index_status(task_id: str) -> dict | None:
    """查询异步索引任务的状态。"""
    return get_task_status(task_id)
