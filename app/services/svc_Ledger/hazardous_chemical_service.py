"""危化品使用企业专项整治 service —— 一行一种危化品的增删改查 + 复合筛选。

数据模型：
    一行 = 一家企业的一种危化品明细（名称/状态/储量/用途/存储等）。
    企业关联 enterprise 表（enterprise_id FK），冗余列 enterprise_name / region / industry / scale 已删除，展示时 JOIN Enterprise 主表。
    与厂中厂表共用筛选协议，前端按企业名纵向合并单元格展示。

本模块基于 BaseRepository（app/db/crud.py）做存取，本文件只写业务规则。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import aliased
from sqlmodel import select, or_

from app.db.crud import BaseRepository
from app.db.models import Enterprise, HazardousChemical
from app.services.svc_Common.filter_service import (
    FieldSpec,
    FilterError,
    FilterGroup,
    FilterLeaf,
    list_with_filter,
)
from app.services.svc_Common.filter_helpers import (
    BOOL_OPS,
    DATE_OPS,
    ENUM_OPS,
    NUM_OPS,
    TEXT_OPS,
    list_with_filter_wrapper,
)

# repo 单例
records = BaseRepository(HazardousChemical)
enterprises = BaseRepository(Enterprise)


class HazardousChemicalError(ValueError):
    """危化品记录操作不合规。"""


# ── 通用复合筛选（filter_service）─────────────────────────────
HAZARDOUS_CHEMICAL_FILTER_FIELDS: dict[str, FieldSpec] = {
    # 虚拟关键词：企业主表 name + 危化品名/存储位置/备注/用途 多字段 OR
    "keyword": FieldSpec(
        key="keyword", label="关键词", type="keyword",
        operators=["like", "like_not"], is_virtual=True,
        virtual_columns=[
            "enterprise_name", "hazchem_name",
            "storage_location", "remark", "usage",
        ],
    ),
    # ── 文本列 ──
    "town": FieldSpec(key="town", label="镇（区、街道）", type="text", operators=TEXT_OPS),
    "hazchem_name": FieldSpec(key="hazchem_name", label="危化品名称", type="text", operators=TEXT_OPS),
    "hazchem_state": FieldSpec(key="hazchem_state", label="危化品状态", type="text", operators=TEXT_OPS),
    "reserve": FieldSpec(key="reserve", label="储量", type="text", operators=TEXT_OPS),
    "max_reserve": FieldSpec(key="max_reserve", label="设计最大储量", type="text", operators=TEXT_OPS),
    "annual_usage": FieldSpec(key="annual_usage", label="年度使用量", type="text", operators=TEXT_OPS),
    "usage": FieldSpec(key="usage", label="危化品用途", type="text", operators=TEXT_OPS),
    "storage_method": FieldSpec(key="storage_method", label="存储方式", type="text", operators=TEXT_OPS),
    "storage_location": FieldSpec(key="storage_location", label="存储位置", type="text", operators=TEXT_OPS),
    "major_hazard": FieldSpec(key="major_hazard", label="是否构成重大危险源", type="text", operators=TEXT_OPS),
    "remark": FieldSpec(key="remark", label="备注", type="text", operators=TEXT_OPS),
    # ── 数字列 ──
    "enterprise_id": FieldSpec(key="enterprise_id", label="企业ID", type="number", operators=["eq", "neq", "is_null", "is_not_null"]),
    "id": FieldSpec(key="id", label="记录ID", type="number", operators=["eq", "neq"]),
}


def get_hazchem_filter_fields() -> list[FieldSpec]:
    """危化品表筛选字段元数据（GET /hazardous-chemicals/meta/filter-fields 用）。"""
    ordered_keys = [
        "keyword",
        "town",
        "hazchem_name", "hazchem_state",
        "reserve", "max_reserve", "annual_usage",
        "usage", "storage_method", "storage_location",
        "major_hazard", "remark",
        "enterprise_id", "id",
    ]
    return [HAZARDOUS_CHEMICAL_FILTER_FIELDS[k] for k in ordered_keys]


# ── 企业名列统一口径：数据源锁死 Enterprise 主表（FK 关联率 100%，冗余列已删除）──
_enterprise_ent = aliased(Enterprise)
_HAZCHEM_JOINS = [
    (_enterprise_ent, _enterprise_ent.id == HazardousChemical.enterprise_id, True),
]
_HAZCHEM_COLUMN_OVERRIDES = {
    "enterprise_name": lambda: _enterprise_ent.name,
}


async def _enrich(r: HazardousChemical) -> dict:
    """给记录附加外键展示名 + 企业属性 —— 纯 Enterprise 主表，无快照回退。"""
    d = r.dict()
    if r.enterprise_id:
        ent = await enterprises.get(r.enterprise_id)
        if ent:
            d["enterprise_name"] = ent.name
            d["enterprise_region"] = ent.region
            d["enterprise_industry"] = ent.industry
        else:
            d["enterprise_name"] = None
            d["enterprise_region"] = None
            d["enterprise_industry"] = None
    else:
        d["enterprise_name"] = None
        d["enterprise_region"] = None
        d["enterprise_industry"] = None
    # 时间字段格式化
    d["created_at"] = (
        r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
    )
    d["updated_at"] = (
        r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else ""
    )
    return d


async def list_records_with_filter(
    filter_root,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """复合条件分页查询（POST /hazardous-chemicals/query 用）。

    排序按 企业名 → 危化品名 → id，保证同企业行连续
    （前端合并单元格依赖）。
    """
    return await list_with_filter_wrapper(
        HazardousChemical,
        records,
        filter_root,
        HAZARDOUS_CHEMICAL_FILTER_FIELDS,
        page=page,
        page_size=page_size,
        order_by=[
            _HAZCHEM_COLUMN_OVERRIDES["enterprise_name"](),
            HazardousChemical.hazchem_name,
            HazardousChemical.id,
        ],
        enrich_fn=_enrich,
        date_field_keys=set(),
        joins=_HAZCHEM_JOINS,
        column_overrides=_HAZCHEM_COLUMN_OVERRIDES,
    )


async def list_records(
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    region: Optional[str] = None,
    hazchem_name: Optional[str] = None,
    hazchem_state: Optional[str] = None,
    major_hazard: Optional[str] = None,
) -> tuple[list[dict], int]:
    """分页列表 + 关键词(Enterprise 主表 name + 危化品字段) + 多维度筛选。返回 (items, total)。"""
    stmt = select(HazardousChemical)
    stmt = stmt.outerjoin(
        _enterprise_ent, _enterprise_ent.id == HazardousChemical.enterprise_id
    )
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            _enterprise_ent.name.ilike(kw),
            HazardousChemical.hazchem_name.ilike(kw),
            HazardousChemical.storage_location.ilike(kw),
            HazardousChemical.remark.ilike(kw),
            HazardousChemical.usage.ilike(kw),
        ))
    if enterprise_name:
        stmt = stmt.where(_enterprise_ent.name.ilike(f"%{enterprise_name}%"))
    if region:
        stmt = stmt.where(_enterprise_ent.region == region)
    if hazchem_name:
        stmt = stmt.where(HazardousChemical.hazchem_name.ilike(f"%{hazchem_name}%"))
    if hazchem_state:
        stmt = stmt.where(HazardousChemical.hazchem_state == hazchem_state)
    if major_hazard:
        stmt = stmt.where(HazardousChemical.major_hazard == major_hazard)

    items, total = await records.list(
        stmt=stmt,
        page=page,
        page_size=page_size,
        order_by=[
            _enterprise_ent.name,
            HazardousChemical.hazchem_name,
            HazardousChemical.id,
        ],
    )
    enriched = [await _enrich(r) for r in items]
    return enriched, total


async def list_by_enterprise(enterprise_id: int, *, limit: int = 200) -> list[dict]:
    """查某企业的危化品明细行（企业档案页用）。"""
    stmt = select(HazardousChemical).where(
        HazardousChemical.enterprise_id == enterprise_id
    )
    items, _ = await records.list(
        stmt=stmt,
        page=1,
        page_size=limit,
        order_by=[HazardousChemical.hazchem_name, HazardousChemical.id],
    )
    return [await _enrich(r) for r in items]


async def get_record(record_id: int) -> Optional[dict]:
    """按 id 查单条（附带外键展示名）。"""
    r = await records.get(record_id)
    if not r:
        return None
    return await _enrich(r)


async def create_record(
    *,
    enterprise_name: Optional[str] = None,   # 兼容前端表单，写入时忽略（数据源锁死 Enterprise 主表）
    enterprise_id: Optional[int] = None,
    region: Optional[str] = None,            # 兼容，忽略
    industry: Optional[str] = None,          # 兼容，忽略
    scale: Optional[str] = None,             # 兼容，忽略
    hazchem_name: Optional[str] = None,
    hazchem_state: Optional[str] = None,
    reserve: Optional[str] = None,
    max_reserve: Optional[str] = None,
    annual_usage: Optional[str] = None,
    usage: Optional[str] = None,
    storage_method: Optional[str] = None,
    storage_location: Optional[str] = None,
    major_hazard: Optional[str] = None,
    remark: Optional[str] = None,
) -> HazardousChemical:
    """新建一条危化品记录。

    已删冗余列（enterprise_name / region / industry / scale）从 Enterprise 主表取，
    本函数里对应形参只作为 router → service 的兼容层，records.create() 里不写。
    """
    return await records.create(
        enterprise_id=enterprise_id,
        hazchem_name=hazchem_name or None,
        hazchem_state=hazchem_state or None,
        reserve=reserve or None,
        max_reserve=max_reserve or None,
        annual_usage=annual_usage or None,
        usage=usage or None,
        storage_method=storage_method or None,
        storage_location=storage_location or None,
        major_hazard=major_hazard or None,
        remark=remark or None,
    )


# update 的哨兵：区分"调用方未提供该字段（不改）"与"显式传 None（置空）"
_UNSET = object()


async def update_record(
    record_id: int,
    *,
    enterprise_name=_UNSET,
    enterprise_id=_UNSET,
    region=_UNSET,
    industry=_UNSET,
    scale=_UNSET,
    hazchem_name=_UNSET,
    hazchem_state=_UNSET,
    reserve=_UNSET,
    max_reserve=_UNSET,
    annual_usage=_UNSET,
    usage=_UNSET,
    storage_method=_UNSET,
    storage_location=_UNSET,
    major_hazard=_UNSET,
    remark=_UNSET,
) -> Optional[HazardousChemical]:
    """按 id 更新记录字段（局部更新语义；显式传 None 置空）。

    冗余列（enterprise_name / region / industry / scale）已从 DB 删除，形参保留作兼容层，
    遍历时跳过，不写入。
    """
    _SKIP = {"enterprise_name", "region", "industry", "scale"}
    updates: dict = {}
    set_null: set[str] = set()
    for k, v in locals().items():
        if k in ("record_id", "updates", "set_null") or v is _UNSET:
            continue
        if k in _SKIP:
            continue
        if v is None:
            set_null.add(k)
        else:
            updates[k] = v
    if not updates and not set_null:
        return await records.get(record_id)
    return await records.update(
        record_id, set_null=set_null, **updates
    )


async def delete_record(record_id: int) -> bool:
    """按 id 删除记录。"""
    return await records.delete(record_id)
