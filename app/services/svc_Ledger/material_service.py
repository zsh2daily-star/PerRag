"""材料服务 —— 文件上传、索引管理、删除、范围同步（V2 契约）。

V2 关键约定：
  - 材料稳定身份是 uid（UUID），写进 Qdrant payload 作为 material_id；
  - 企业关联用 enterprise_codes（text[]，弱引用 enterprise.code，可多家）；
  - 范围三形态：enterprise_codes='{}' 公共通用 / scope_industry 或 scope_region 定向 /
    enterprise_codes 非空涉及具体企业；B 与 C 可并存，A 与 B∪C 二选一；
  - document_chunk 表已删除，chunk 元数据全在 Qdrant payload，service 不再同步；
  - scope 变更先改 SQL、再 Qdrant set_payload 原地更新，失败打脏标记 scope_sync_pending。

流程：
    upload_material()  → 落本地文件 + 入 material 表（生成 uid，status=pending）
    index_material()   → 调 rag_core.index_file(uid)，status 流转 indexing→indexed/failed
    update_scope()     → 改 SQL scope 字段 + 同步 Qdrant payload
    delete_material()  → 删 Qdrant 数据 + DB 记录 + 本地文件
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings
from app.db.crud import BaseRepository
from app.db.models import Material

logger = logging.getLogger(__name__)

# repo 单例
materials = BaseRepository(Material)

# 范围三形态互斥校验：A（公共通用）与 B∪C（定向/涉及）二选一
_SCOPE_A = "public"      # enterprise_codes 为空且 scope_* 为空
_SCOPE_BC = "targeted"   # scope_industry/region 或 enterprise_codes 非空


class MaterialError(ValueError):
    """材料操作不合规。"""


def _guess_mime(filename: str) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _normalize_codes(codes: Optional[list[str]]) -> list[str]:
    """enterprise_codes 统一为大写列表，去重。"""
    if not codes:
        return []
    seen = []
    for c in codes:
        c = (c or "").strip().upper()
        if c and c not in seen:
            seen.append(c)
    return seen


def _validate_scope(
    enterprise_codes: list[str],
    scope_industry: Optional[str],
    scope_region: Optional[str],
) -> None:
    """范围三形态互斥校验：A 与 B∪C 二选一。"""
    is_a = not enterprise_codes and not scope_industry and not scope_region
    is_bc = bool(enterprise_codes) or bool(scope_industry) or bool(scope_region)
    if not is_a and not is_bc:
        # 不可能同时不满足，保底
        return
    # A 和 BC 必须二选一（其实 is_a 和 is_bc 天然互斥，这里只是显式化）
    if is_a and is_bc:
        raise MaterialError("范围冲突：公共通用与定向/涉及企业不能同时设置")


async def upload_material(
    *,
    filename: str,
    file_data: bytes,
    enterprise_codes: Optional[list[str]] = None,
    scope_industry: Optional[str] = None,
    scope_region: Optional[str] = None,
    uploaded_by: Optional[int] = None,
    qdrant_collection: Optional[str] = None,
    source_format: Optional[str] = None,
) -> Material:
    """上传文件 → 落本地 + 入 material 表（生成 uid，status=pending）。

    enterprise_codes/scope_industry/scope_region 三参数决定范围形态，
    互斥校验：公共通用（全空）与定向/涉及（非空）二选一。
    """
    codes = _normalize_codes(enterprise_codes)
    _validate_scope(codes, scope_industry, scope_region)

    # 文件哈希与存储路径：无文件时只登记，不落地
    if file_data:
        storage_dir = Path(settings.import_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_hash = hashlib.sha256(file_data).hexdigest()
        storage_path = storage_dir / f"{file_hash}_{filename}"
        storage_path.write_bytes(file_data)
    else:
        file_hash = None
        storage_path = ""

    # 格式：优先用传入的 source_format，否则从文件名后缀提取
    if source_format:
        source_format = source_format.lower().lstrip(".")
    else:
        ext = Path(filename).suffix.lower().lstrip(".")
        source_format = ext or "text"
    mime_type = _guess_mime(filename)

    from app.core_Qdrant.config import RAG_CORE_LOAD_CONFIG
    collection = qdrant_collection or RAG_CORE_LOAD_CONFIG.qdrant_collection

    material = await materials.create(
        uid=str(uuid.uuid4()),
        filename=filename,
        storage_path=str(storage_path),
        file_hash=file_hash,
        mime_type=mime_type,
        source_format=source_format,
        uploaded_by=uploaded_by,
        qdrant_collection=collection,
        status="pending",
        enterprise_codes=json.dumps(codes, ensure_ascii=False),
        scope_industry=scope_industry or None,
        scope_region=scope_region or None,
        scope_sync_pending=False,
    )

    logger.info("材料已上传: %s (uid=%s)", filename, material.uid)
    return material


async def index_material(uid: str) -> int:
    """索引材料 → 调 rag_core.index_file(uid)，status 流转 indexing→indexed/failed。

    返回 chunk 数量。
    """
    material = await materials.get_by(uid=uid)
    if not material:
        raise MaterialError(f"材料不存在: uid={uid}")

    # 标记索引中
    material = await materials.update(material.id, status="indexing")

    try:
        from app.core_Qdrant import index_file

        chunk_count = index_file(
            Path(material.storage_path),
            material_id=material.uid,  # V2: 用 uid 作为 Qdrant material_id
        )

        # 索引成功：indexed + 同步 scope 到 Qdrant payload
        await materials.update(
            material.id,
            status="indexed",
            indexed_at=datetime.now(),
        )
        # 同步 scope payload（首次索引后写入）
        await _sync_scope_to_qdrant(material)

        logger.info("材料索引完成: %s (uid=%s, %d chunks)", material.filename, material.uid, chunk_count)
        return chunk_count

    except Exception as e:
        await materials.update(material.id, status="failed")
        logger.error("材料索引失败: %s (uid=%s) — %s", material.filename, material.uid, e)
        raise


async def reindex_material(uid: str) -> int:
    """重建索引：先删旧索引再重新索引。"""
    material = await materials.get_by(uid=uid)
    if not material:
        raise MaterialError(f"材料不存在: uid={uid}")

    # 删旧索引
    from app.core_Qdrant import delete_document
    try:
        delete_document(material.filename, collection=material.qdrant_collection)
    except Exception as e:
        logger.warning("删除旧索引失败（继续重建）: %s", e)

    return await index_material(uid)


async def list_materials(
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[dict], int]:
    """分页列表 + 关键词(文件名/uid) + 状态筛选。

    返回的每条记录附带 uploaded_by_name，enterprise_codes 解析为列表。
    """
    from sqlmodel import select, or_

    stmt = select(Material)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            Material.filename.like(kw),
            Material.uid.like(kw),
        ))
    if status:
        stmt = stmt.where(Material.status == status)

    items, total = await materials.list(
        stmt=stmt, page=page, page_size=page_size,
        order_by=[Material.created_at.desc()],
    )
    enriched = [await _enrich(m) for m in items]
    return enriched, total


async def _enrich(m: Material) -> dict:
    """给材料附加展示字段：uploaded_by_name、enterprise_codes 转列表。"""
    from app.db.crud import BaseRepository as _Repo
    from app.db.models import Staff

    staffs_repo = _Repo(Staff)
    d = m.dict()
    # enterprise_codes 从 JSON 字符串解析为列表
    try:
        d["enterprise_codes"] = json.loads(m.enterprise_codes) if m.enterprise_codes else []
    except (json.JSONDecodeError, TypeError):
        d["enterprise_codes"] = []
    # 上传人姓名
    if m.uploaded_by:
        u = await staffs_repo.get(m.uploaded_by)
        d["uploaded_by_name"] = u.name if u else None
    else:
        d["uploaded_by_name"] = None
    return d


async def get_by_uid(uid: str) -> Optional[dict]:
    """按 uid 查单条（带 enrich）。"""
    m = await materials.get_by(uid=uid)
    if not m:
        return None
    return await _enrich(m)


async def update_scope(
    uid: str,
    *,
    enterprise_codes: Optional[list[str]] = None,
    scope_industry: Optional[str] = None,
    scope_region: Optional[str] = None,
) -> Optional[dict]:
    """更新材料范围（先改 SQL，再同步 Qdrant payload；失败打脏标记）。

    三参数任一传入即更新该字段；传入空列表/空串表示清空。
    """
    m = await materials.get_by(uid=uid)
    if not m:
        return None

    codes = _normalize_codes(enterprise_codes) if enterprise_codes is not None else None
    new_codes = codes if codes is not None else (
        json.loads(m.enterprise_codes) if m.enterprise_codes else []
    )
    new_industry = scope_industry if scope_industry is not None else m.scope_industry
    new_region = scope_region if scope_region is not None else m.scope_region

    _validate_scope(new_codes, new_industry, new_region)

    # 先改 SQL
    updates = {}
    if codes is not None:
        updates["enterprise_codes"] = json.dumps(new_codes, ensure_ascii=False)
    if scope_industry is not None:
        updates["scope_industry"] = scope_industry or None
    if scope_region is not None:
        updates["scope_region"] = scope_region or None
    updates["scope_sync_pending"] = True  # 先标记待同步

    await materials.update(m.id, **updates)

    # 再同步 Qdrant payload
    ok = await _sync_scope_to_qdrant(await materials.get(m.id))
    if not ok:
        logger.warning("材料范围同步 Qdrant 失败，已打脏标记: uid=%s", uid)
    else:
        await materials.update(m.id, scope_sync_pending=False)

    return await _enrich(await materials.get(m.id))


async def _sync_scope_to_qdrant(material: Material) -> bool:
    """把 scope 字段同步到 Qdrant payload（原地更新，不重建向量）。

    成功返回 True，失败返回 False（由调用方打脏标记）。
    """
    try:
        from app.core_Qdrant.config import RAG_CORE_LOAD_CONFIG
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qmodels

        col = material.qdrant_collection or RAG_CORE_LOAD_CONFIG.qdrant_collection
        client = QdrantClient(
            host=RAG_CORE_LOAD_CONFIG.qdrant_host,
            port=RAG_CORE_LOAD_CONFIG.qdrant_port,
        )
        # 检查 collection
        existing = {c.name for c in client.get_collections().collections}
        if col not in existing:
            return True  # 没有 collection 无需同步

        codes = []
        try:
            codes = json.loads(material.enterprise_codes) if material.enterprise_codes else []
        except (json.JSONDecodeError, TypeError):
            codes = []

        payload = {
            "material_uid": material.uid,
            "enterprise_codes": codes,
            "scope_industry": material.scope_industry,
            "scope_region": material.scope_region,
        }
        client.set_payload(
            collection_name=col,
            payload=payload,
            points=qmodels.FilterSelector(
                filter=qmodels.Filter(must=[
                    qmodels.FieldCondition(
                        key="material_id",
                        match=qmodels.MatchValue(value=material.uid),
                    )
                ])
            ),
        )
        return True
    except Exception as e:
        logger.warning("Qdrant scope 同步失败: uid=%s — %s", material.uid, e)
        return False


async def delete_material(uid: str) -> bool:
    """删除材料：Qdrant 数据 + DB 记录 + 本地文件。"""
    m = await materials.get_by(uid=uid)
    if not m:
        return False

    # 删 Qdrant 索引
    try:
        from app.core_Qdrant import delete_document
        delete_document(m.filename, collection=m.qdrant_collection)
    except Exception as e:
        logger.warning("删除 Qdrant 索引失败: %s", e)

    # 删本地文件
    try:
        Path(m.storage_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("删除本地文件失败: %s — %s", m.storage_path, e)

    # 删 DB 记录
    await materials.delete(m.id)
    logger.info("材料已删除: %s (uid=%s)", m.filename, uid)
    return True
