"""厂中厂专项整治 service —— 出租方-承租方租赁关系的增删改查 + 复合筛选。

数据模型：
    一行 = 一对（出租方, 承租方）+ 该次专项整治的隐患与整改。
    出租方/承租方都关联 enterprise 表（enterprise_id FK），原文姓名保留。
    多条隐患以顿号分隔的文本存于 hazard_description；如需逐条统计可后续拆分。
    inspection_batch 聚合同批次（如 "2026-厂中厂消防"），与企业表/巡查表共用筛选协议。

本模块基于 BaseRepository（app/db/crud.py）做存取，本文件只写业务规则。
"""
from datetime import date, datetime
from typing import Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import select, or_

from app.db.crud import BaseRepository
from app.db.models import Enterprise, FactoryInFactory
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
records = BaseRepository(FactoryInFactory)
enterprises = BaseRepository(Enterprise)


class FactoryInFactoryError(ValueError):
    """厂中厂记录操作不合规。"""


# ── 通用复合筛选（filter_service）─────────────────────────────
# 操作符常量已抽到 app.services.svc_Common.filter_helpers（4 表共用）。
FACTORY_IN_FACTORY_FILTER_FIELDS: dict[str, FieldSpec] = {
    # 虚拟关键词：企业主表 name/person_in_charge + 隐患/排查人/批次 多字段 OR
    "keyword": FieldSpec(
        key="keyword", label="关键词", type="keyword",
        operators=["like", "like_not"], is_virtual=True,
        virtual_columns=[
            "lessor_name", "tenant_name",
            "lessor_hazard_description", "tenant_hazard_description",
            "inspectors", "rect_supervisor", "inspection_batch", "remark",
        ],
    ),
    # ── 文本列 ──
    "inspection_batch": FieldSpec(key="inspection_batch", label="检查批次", type="text", operators=TEXT_OPS),
    "region": FieldSpec(key="region", label="县市区", type="text", operators=TEXT_OPS),
    "address": FieldSpec(key="address", label="具体地址", type="text", operators=TEXT_OPS),
    "lessor_name": FieldSpec(key="lessor_name", label="出租方", type="text", operators=TEXT_OPS),
    "tenant_name": FieldSpec(key="tenant_name", label="承租方", type="text", operators=TEXT_OPS),
    # 联系人（映射 Enterprise.person_in_charge，见 _FACTORY_COLUMN_OVERRIDES）
    "lessor_contact_person": FieldSpec(key="lessor_contact_person", label="出租方联系人", type="text", operators=TEXT_OPS),
    "tenant_contact_person": FieldSpec(key="tenant_contact_person", label="承租方联系人", type="text", operators=TEXT_OPS),
    "lessor_use_area": FieldSpec(key="lessor_use_area", label="出租方面积", type="text", operators=TEXT_OPS),
    "lessor_hazard_description": FieldSpec(key="lessor_hazard_description", label="出租方隐患", type="text", operators=TEXT_OPS),
    "lessor_rect_status": FieldSpec(key="lessor_rect_status", label="出租方整改", type="text", operators=TEXT_OPS),
    "tenant_hazard_description": FieldSpec(key="tenant_hazard_description", label="承租方隐患", type="text", operators=TEXT_OPS),
    "tenant_rect_status": FieldSpec(key="tenant_rect_status", label="承租方整改", type="text", operators=TEXT_OPS),
    "site_category": FieldSpec(key="site_category", label="场所类别", type="text", operators=TEXT_OPS),
    "inspectors": FieldSpec(key="inspectors", label="排查人员", type="text", operators=TEXT_OPS),
    "rect_supervisor": FieldSpec(key="rect_supervisor", label="督改人员", type="text", operators=TEXT_OPS),
    "remark": FieldSpec(key="remark", label="备注", type="text", operators=TEXT_OPS),
    # ── 枚举列 ──
    "agreement_signed": FieldSpec(
        key="agreement_signed", label="是否签订协议", type="bool", operators=BOOL_OPS,
    ),
    # ── 日期列 ──
    "inspection_date": FieldSpec(key="inspection_date", label="排查日期", type="date", operators=DATE_OPS),
    # ── 数字列 ──
    "tenant_area": FieldSpec(key="tenant_area", label="承租面积", type="number", operators=NUM_OPS),
    "lessor_enterprise_id": FieldSpec(key="lessor_enterprise_id", label="出租方ID", type="number", operators=["eq", "neq", "is_null", "is_not_null"]),
    "tenant_enterprise_id": FieldSpec(key="tenant_enterprise_id", label="承租方ID", type="number", operators=["eq", "neq", "is_null", "is_not_null"]),
    "id": FieldSpec(key="id", label="记录ID", type="number", operators=["eq", "neq"]),
}


def get_factory_in_factory_filter_fields() -> list[FieldSpec]:
    """厂中厂表筛选字段元数据（GET /factory-in-factory/meta/filter-fields 用）。"""
    ordered_keys = [
        "keyword",
        "lessor_name", "lessor_contact_person",
        "tenant_name", "tenant_contact_person",
        "inspection_batch", "region", "address",
        "lessor_hazard_description", "tenant_hazard_description",
        "lessor_rect_status", "tenant_rect_status",
        "site_category", "inspectors", "rect_supervisor", "remark",
        "lessor_use_area", "tenant_area",
        "agreement_signed",
        "inspection_date",
        "lessor_enterprise_id", "tenant_enterprise_id", "id",
    ]
    return [FACTORY_IN_FACTORY_FILTER_FIELDS[k] for k in ordered_keys]


# 日期字段与 coerce 逻辑已抽到 app.services.svc_Common.filter_helpers
# （date_field_keys 由 list_records_with_filter 调用时传入）。


# ── 出租方/承租方列统一口径 ─────────────────────────────────
# 数据源锁死 Enterprise 主表（FK 关联率 100%，冗余列已删除）。
# 出租方/承租方是两个独立外键，用别名各做一次外连接。
_lessor_ent = aliased(Enterprise)
_tenant_ent = aliased(Enterprise)
_FACTORY_JOINS = [
    (_lessor_ent, _lessor_ent.id == FactoryInFactory.lessor_enterprise_id, True),
    (_tenant_ent, _tenant_ent.id == FactoryInFactory.tenant_enterprise_id, True),
]
_FACTORY_COLUMN_OVERRIDES = {
    "lessor_name": lambda: _lessor_ent.name,
    "tenant_name": lambda: _tenant_ent.name,
    # 联系人筛选 → Enterprise 主表 person_in_charge（"姓名 电话" 整串，LIKE 双向兼容）
    "lessor_contact_person": lambda: _lessor_ent.person_in_charge,
    "tenant_contact_person": lambda: _tenant_ent.person_in_charge,
}


async def _enrich(r: FactoryInFactory) -> dict:
    """给记录附加外键展示名 + 联系人 —— 纯 Enterprise 主表，无快照回退。"""
    d = r.dict()

    def _split_contact(s: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """把 Enterprise.person_in_charge（如 "曾宇周 18705103434"）拆成 (name, phone)。"""
        if not s:
            return None, None
        import re as _re
        m = _re.search(r"(\d{11,15})\s*$", s)
        if m:
            phone = m.group(1)
            name = s[: m.start()].strip(" -，,、") or None
            return name, phone
        return s.strip(), None

    # ── 出租方 ──
    if r.lessor_enterprise_id:
        ent = await enterprises.get(r.lessor_enterprise_id)
        if ent:
            d["lessor_name"] = ent.name
            name, phone = _split_contact(ent.person_in_charge)
            d["lessor_contact_person"] = name
            d["lessor_contact_phone"] = phone
        else:
            d["lessor_name"] = None
            d["lessor_contact_person"] = None
            d["lessor_contact_phone"] = None
    else:
        d["lessor_name"] = None
        d["lessor_contact_person"] = None
        d["lessor_contact_phone"] = None

    # ── 承租方 ──
    if r.tenant_enterprise_id:
        ent = await enterprises.get(r.tenant_enterprise_id)
        if ent:
            d["tenant_name"] = ent.name
            name, phone = _split_contact(ent.person_in_charge)
            d["tenant_contact_person"] = name
            d["tenant_contact_phone"] = phone
        else:
            d["tenant_name"] = None
            d["tenant_contact_person"] = None
            d["tenant_contact_phone"] = None
    else:
        d["tenant_name"] = None
        d["tenant_contact_person"] = None
        d["tenant_contact_phone"] = None

    # 时间字段格式化
    d["inspection_date"] = (
        r.inspection_date.isoformat() if r.inspection_date else None
    )
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
    """复合条件分页查询（POST /factory-in-factory/query 用）。

    排序按 排查日期倒序 → 出租方 → 承租方 → id，保证同出租方行连续
    （前端合并单元格依赖）。

    日期规整 + 查询 + enrich 由 `filter_helpers.list_with_filter_wrapper`
    统一封装；本表特化的外键补名/时间格式化逻辑在 `_enrich`。
    """
    return await list_with_filter_wrapper(
        FactoryInFactory,
        records,
        filter_root,
        FACTORY_IN_FACTORY_FILTER_FIELDS,
        page=page,
        page_size=page_size,
        order_by=[
            FactoryInFactory.inspection_date.desc().nullslast(),
            _FACTORY_COLUMN_OVERRIDES["lessor_name"](),
            _FACTORY_COLUMN_OVERRIDES["tenant_name"](),
            FactoryInFactory.id,
        ],
        enrich_fn=_enrich,
        date_field_keys={"inspection_date"},
        joins=_FACTORY_JOINS,
        column_overrides=_FACTORY_COLUMN_OVERRIDES,
    )


async def list_records(
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    inspection_batch: Optional[str] = None,
    region: Optional[str] = None,
    lessor_name: Optional[str] = None,
    tenant_name: Optional[str] = None,
    site_category: Optional[str] = None,
    inspection_date: Optional[date] = None,
) -> tuple[list[dict], int]:
    """分页列表 + 关键词(Enterprise 主表 name/person_in_charge + 厂中厂关系字段)。"""
    stmt = select(FactoryInFactory)
    stmt = stmt.outerjoin(
        _lessor_ent, _lessor_ent.id == FactoryInFactory.lessor_enterprise_id
    ).outerjoin(
        _tenant_ent, _tenant_ent.id == FactoryInFactory.tenant_enterprise_id
    )
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            _lessor_ent.name.ilike(kw),
            _lessor_ent.person_in_charge.ilike(kw),
            _tenant_ent.name.ilike(kw),
            _tenant_ent.person_in_charge.ilike(kw),
            FactoryInFactory.lessor_hazard_description.ilike(kw),
            FactoryInFactory.tenant_hazard_description.ilike(kw),
            FactoryInFactory.inspection_batch.ilike(kw),
        ))
    if inspection_batch:
        stmt = stmt.where(FactoryInFactory.inspection_batch == inspection_batch)
    if region:
        stmt = stmt.where(FactoryInFactory.region == region)
    if lessor_name:
        stmt = stmt.where(_lessor_ent.name.ilike(f"%{lessor_name}%"))
    if tenant_name:
        stmt = stmt.where(_tenant_ent.name.ilike(f"%{tenant_name}%"))
    if site_category:
        stmt = stmt.where(FactoryInFactory.site_category == site_category)
    if inspection_date:
        stmt = stmt.where(FactoryInFactory.inspection_date == inspection_date)

    items, total = await records.list(
        stmt=stmt,
        page=page,
        page_size=page_size,
        order_by=[
            FactoryInFactory.inspection_date.desc().nullslast(),
            _lessor_ent.name,
            _tenant_ent.name,
            FactoryInFactory.id,
        ],
    )
    enriched = [await _enrich(r) for r in items]
    return enriched, total


async def list_by_enterprise(enterprise_id: int, *, limit: int = 100) -> list[dict]:
    """查某企业作为出租方或承租方的厂中厂关系行（企业档案页用）。"""
    stmt = select(FactoryInFactory).where(or_(
        FactoryInFactory.lessor_enterprise_id == enterprise_id,
        FactoryInFactory.tenant_enterprise_id == enterprise_id,
    ))
    items, _ = await records.list(
        stmt=stmt,
        page=1,
        page_size=limit,
        order_by=[
            FactoryInFactory.inspection_date.desc().nullslast(),
            FactoryInFactory.id,
        ],
    )
    return [await _enrich(r) for r in items]


async def list_batches() -> list[str]:
    """全部 inspection_batch 去重列表（前端下拉用）。"""
    stmt = select(FactoryInFactory.inspection_batch).distinct()
    from app.db.database import get_session_factory
    async with get_session_factory()() as s:
        rows = (await s.execute(stmt)).scalars().all()
    return sorted([r for r in rows if r])


async def get_record(record_id: int) -> Optional[dict]:
    """按 id 查单条（附带外键展示名）。"""
    r = await records.get(record_id)
    if not r:
        return None
    return await _enrich(r)


async def create_record(
    *,
    inspection_batch: str,
    lessor_name: Optional[str] = None,       # 兼容前端表单，写入时忽略（数据源锁死 Enterprise 主表）
    tenant_name: Optional[str] = None,        # 同上
    lessor_enterprise_id: Optional[int] = None,
    tenant_enterprise_id: Optional[int] = None,
    region: Optional[str] = None,
    address: Optional[str] = None,
    lessor_contact_person: Optional[str] = None,   # 兼容，忽略
    lessor_contact_phone: Optional[str] = None,     # 兼容，忽略
    lessor_use_area: Optional[str] = None,
    lessor_hazard_description: Optional[str] = None,
    lessor_rect_status: Optional[str] = None,
    tenant_area: Optional[float] = None,
    tenant_contact_person: Optional[str] = None,    # 兼容，忽略
    tenant_contact_phone: Optional[str] = None,      # 兼容，忽略
    agreement_signed: Optional[bool] = None,
    tenant_hazard_description: Optional[str] = None,
    tenant_rect_status: Optional[str] = None,
    site_category: Optional[str] = None,
    inspection_date: Optional[date] = None,
    inspectors: Optional[str] = None,
    rect_supervisor: Optional[str] = None,
    remark: Optional[str] = None,
) -> FactoryInFactory:
    """新建一条厂中厂记录。

    已删冗余列（lessor_name/tenant_name/contact_*）从 Enterprise 主表取，
    本函数里对应形参只作为 router → service 的兼容层，records.create() 里不写。
    """
    if not inspection_batch or not inspection_batch.strip():
        raise FactoryInFactoryError("检查批次不能为空")
    return await records.create(
        inspection_batch=inspection_batch.strip(),
        lessor_enterprise_id=lessor_enterprise_id,
        tenant_enterprise_id=tenant_enterprise_id,
        region=region or None,
        address=address or None,
        lessor_use_area=lessor_use_area or None,
        lessor_hazard_description=lessor_hazard_description or None,
        lessor_rect_status=lessor_rect_status or None,
        tenant_area=tenant_area,
        agreement_signed=agreement_signed,
        tenant_hazard_description=tenant_hazard_description or None,
        tenant_rect_status=tenant_rect_status or None,
        site_category=site_category or None,
        inspection_date=inspection_date,
        inspectors=inspectors or None,
        rect_supervisor=rect_supervisor or None,
        remark=remark or None,
    )


# update 的哨兵：区分"调用方未提供该字段（不改）"与"显式传 None（置空）"
_UNSET = object()


async def update_record(
    record_id: int,
    *,
    inspection_batch=_UNSET,
    lessor_name=_UNSET,
    tenant_name=_UNSET,
    lessor_enterprise_id=_UNSET,
    tenant_enterprise_id=_UNSET,
    region=_UNSET,
    address=_UNSET,
    lessor_contact_person=_UNSET,
    lessor_contact_phone=_UNSET,
    lessor_use_area=_UNSET,
    lessor_hazard_description=_UNSET,
    lessor_rect_status=_UNSET,
    tenant_area=_UNSET,
    tenant_contact_person=_UNSET,
    tenant_contact_phone=_UNSET,
    agreement_signed=_UNSET,
    tenant_hazard_description=_UNSET,
    tenant_rect_status=_UNSET,
    site_category=_UNSET,
    inspection_date=_UNSET,
    inspectors=_UNSET,
    rect_supervisor=_UNSET,
    remark=_UNSET,
) -> Optional[FactoryInFactory]:
    """按 id 更新记录字段（局部更新语义；显式传 None 置空）。

    冗余列（lessor_name/tenant_name/contact_*）已从 DB 删除，形参保留作兼容层，
    遍历时跳过，不写入。
    """
    _SKIP = {"lessor_name", "tenant_name",
             "lessor_contact_person", "lessor_contact_phone",
             "tenant_contact_person", "tenant_contact_phone"}
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
    # inspection_batch 不允许置空
    if "inspection_batch" in set_null:
        raise FactoryInFactoryError("检查批次不能为空")
    set_null.discard("inspection_batch")
    if not updates and not set_null:
        return await records.get(record_id)
    return await records.update(
        record_id, set_null=set_null, **updates
    )


async def delete_record(record_id: int) -> bool:
    """按 id 删除记录。"""
    return await records.delete(record_id)
