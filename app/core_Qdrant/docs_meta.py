"""文档元数据查询（RAG Core 对外契约 docs_meta 组）—— Qdrant 中的文档/集合视图。

集中提供以下能力（原先散落在 app.main 与 app.tools 中、直接操作 Qdrant 的代码）：
- docs_meta(collection)：scroll 全量去重，列出文档（filename / source / chunks 数）
- 文档列表缓存：高频只读场景复用，索引/删除后由 refresh_docs_meta_cache() 刷新
- document_meta(filename, collection)：按文件名精确查询单个文档
- get_document_chunks(filename, collection, max_chunks)：取某文档的 chunk 原文
- list_collections()：列出所有非稀疏 collection
- delete_document(filename, collection)：按文件名删除 dense + sparse 两路向量
"""

import logging
import threading

from qdrant_client import QdrantClient

from app.core_Qdrant.config import RAG_CORE_LOAD_CONFIG as core_config

logger = logging.getLogger(__name__)


def _get_client() -> QdrantClient:
    """新建 Qdrant 客户端（元数据查询为短连接，与原 tools/main 实现一致）。"""
    return QdrantClient(host=core_config.qdrant_host, port=core_config.qdrant_port)


def _resolve_collection(collection: str | None) -> str:
    return collection or core_config.qdrant_collection


# ── 文档列表（全量去重）──────────────────────────────────────


def docs_meta(collection: str | None = None) -> list[dict]:
    """从 Qdrant 提取指定 collection 的去重文档列表。

    遍历所有点的 payload，按 filename 去重，
    返回文件名、来源路径、chunk 数量等信息。

    实现细节：
    - 使用 scroll（游标分页）而非 search，因为不需要语义匹配
    - 每批取 100 条，最多 500 批（= 50,000 条上限），避免无限循环
    - 按 filename 去重并统计每个文件的 chunk 数
    """
    col = _resolve_collection(collection)
    client = _get_client()

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


# ── 文档列表缓存 ──────────────────────────────────────────

# 启动时从 Qdrant 拉取并缓存，后续请求直接用缓存不查 Qdrant。
# 索引/删除操作后自动刷新，避免高频调用时每次都 scroll 全库。
_doc_list_cache: list[dict] | None = None
_doc_list_cache_lock = threading.Lock()


def refresh_docs_meta_cache(collection: str | None = None) -> list[dict]:
    """重新构建文档列表缓存 —— 从 Qdrant scroll 全量数据。

    启动时和索引变更后调用。缓存 miss 时 get_docs_meta_cached 也会自动构建。
    """
    global _doc_list_cache
    with _doc_list_cache_lock:
        docs = docs_meta(collection)
        _doc_list_cache = docs
        logger.info("文档列表缓存已刷新: %d 个文档", len(docs))
        return docs


# 旧名称兼容（app 层历史调用名）
build_doc_list_cache = refresh_docs_meta_cache


def get_docs_meta_cached(force_refresh: bool = False) -> list[dict]:
    """获取缓存的文档列表。首次调用/缓存为空时自动从 Qdrant 构建。

    与 docs_meta() 的区别：
    - 首次构建后后续调用走缓存，不查 Qdrant
    - 适用于高频调用场景
    - force_refresh=True 可强制重建（索引变更后调用）
    """
    global _doc_list_cache
    if _doc_list_cache is None or force_refresh:
        return refresh_docs_meta_cache()
    return _doc_list_cache


# ── 单文档精确查询 ──────────────────────────────────────────


def document_meta(filename: str, collection: str | None = None) -> dict | None:
    """按文件名精确查询文档是否存在及 chunk 数。

    返回 {"filename", "chunks", "source"}，不存在返回 None。
    """
    col = _resolve_collection(collection)
    client = _get_client()
    existing = {c.name for c in client.get_collections().collections}
    if col not in existing:
        return None
    count = client.count(
        collection_name=col,
        count_filter={"must": [{"key": "filename", "match": {"value": filename}}]},
        exact=True,
    )
    if count.count == 0:
        return None
    # 拿一条看 source
    points = client.scroll(
        collection_name=col, limit=1,
        with_payload=["source"],
        scroll_filter={"must": [{"key": "filename", "match": {"value": filename}}]},
    )[0]
    source = points[0].payload.get("source", "") if points else ""
    return {"filename": filename, "chunks": count.count, "source": source}


# ── 文档 chunk 原文 ─────────────────────────────────────────


def get_document_chunks(
    filename: str, collection: str | None = None, max_chunks: int = 20,
) -> list[str]:
    """获取文档 chunk 原文（按 filename 精确过滤，dense 向量召回）。

    用文件名作为 query 做 dense 检索 + payload 过滤，
    返回该文档的 chunk 文本列表（最多 max_chunks 条）。
    """
    from app.core_Qdrant.models import get_embed_model
    from app.core_Qdrant.retriever import _extract_text_from_payload

    col = _resolve_collection(collection)
    embed_model = get_embed_model()
    client = _get_client()
    query_vector = embed_model.get_query_embedding(filename)
    results = client.query_points(
        collection_name=col,
        query=query_vector,
        using="text-dense",
        limit=max_chunks,
        query_filter={
            "must": [{"key": "filename", "match": {"value": filename}}],
        },
        with_payload=True,
    )
    return [_extract_text_from_payload(p.payload) for p in results.points]


# ── 集合管理 ────────────────────────────────────────────────


def list_collections() -> list[str]:
    """列出所有 Qdrant collections（排除 {collection}_sparse 稀疏集合），按名排序。"""
    client = _get_client()
    cols = client.get_collections().collections
    return sorted(c.name for c in cols if not c.name.endswith("_sparse"))


# ── 文档删除 ────────────────────────────────────────────────


def delete_document(filename: str, collection: str | None = None) -> None:
    """按文件名删除文档索引（dense + sparse 两路）。

    不会删除磁盘上的原始文件 —— 只是解除索引。
    """
    from app.core_Qdrant.indexer import get_indexer

    indexer = get_indexer()
    indexer._collection_override = _resolve_collection(collection)
    indexer._delete_by_filename(filename)
    indexer._delete_sparse_by_filename(filename)


# ── 可追溯引用：按 material_id + page 定位原文片段 ──────────


def get_evidence(
    material_id: str,
    page: int | None = None,
    collection: str | None = None,
    max_chunks: int = 50,
) -> list[dict]:
    """按 material_id + page 检索原始 chunk，按 chunk_index 排序返回。

    用于 RAG 回答的可追溯引用 —— 答案中标注 [来源: 文档名 第 N 页] 时，
    可通过本方法获取该页的原文片段供用户验证。

    参数:
        material_id: 阶段 B 由 indexer 写入的 sha1 hash 或阶段 C 的 DB material.id
        page:        可选页码过滤，None=返回该材料所有 chunk
        collection:   可选 Qdrant collection，None=使用默认值
        max_chunks:  最多返回多少条 chunk（防止大文档一次性拉太多）

    返回:
        list[dict]: [{text, source, filename, page_number, section_title,
                      chunk_index, material_id}, ...] 按 chunk_index 排序
    """
    col = _resolve_collection(collection)
    client = _get_client()

    # 检查 collection 是否存在
    existing = {c.name for c in client.get_collections().collections}
    if col not in existing:
        return []

    # 构建 Qdrant filter: must match material_id, 可选 match page_number
    from qdrant_client.http import models as qmodels

    must_conditions = [
        qmodels.FieldCondition(
            key="material_id",
            match=qmodels.MatchValue(value=material_id),
        )
    ]
    if page is not None:
        must_conditions.append(
            qmodels.FieldCondition(
                key="page_number",
                match=qmodels.MatchValue(value=page),
            )
        )

    scroll_filter = qmodels.Filter(must=must_conditions)

    # scroll 遍历所有匹配的 chunk
    results: list[dict] = []
    offset = None
    for _ in range(100):  # 最多 100 批 × 50 = 5000 条
        resp = client.scroll(
            collection_name=col,
            limit=50,
            offset=offset,
            scroll_filter=scroll_filter,
            with_payload=True,
        )
        points = resp[0]
        if not points:
            break
        for p in points:
            payload = p.payload or {}
            results.append({
                "text": payload.get("text", ""),
                "source": payload.get("source", ""),
                "filename": payload.get("filename", ""),
                "page_number": payload.get("page_number"),
                "section_title": payload.get("section_title"),
                "chunk_index": payload.get("chunk_index", 0),
                "material_id": payload.get("material_id"),
            })
        offset = resp[1]
        if offset is None:
            break

    # 按 chunk_index 排序
    results.sort(key=lambda x: x.get("chunk_index", 0))
    return results[:max_chunks]
