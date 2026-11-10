"""巡查台账 service —— 一巡查多隐患模型的增删改查 + 批次聚合 + 状态流转。

数据模型：
    一次到点巡查 → 生成一个巡查单号（inspection_batch，如 INS-20260911-001），
    可覆盖多企业、每企业多条隐患。每行 = 一条隐患 + 其整改信息，
    巡查信息（日期/地点/巡查人）在同批次内共享。
    状态流转：open → rectifying → closed（每条隐患独立）。

本模块基于 BaseRepository（app/db/crud.py）做存取，本文件只写业务规则。
"""
import re
from datetime import date, datetime
from typing import Optional, Union

from sqlalchemy import func, update
from sqlmodel import select, or_

from app.db.crud import BaseRepository
from app.db.models import Enterprise, InspectionRecord, Staff
from app.services.svc_Common.filter_service import (
    FieldSpec,
    FilterError,
    FilterGroup,
    FilterLeaf,
    list_with_filter,
)
from app.services.svc_Common.filter_helpers import (
    DATE_OPS,
    ENUM_OPS,
    NUM_OPS,
    TEXT_OPS,
    list_with_filter_wrapper,
)

# repo 单例
records = BaseRepository(InspectionRecord)
enterprises = BaseRepository(Enterprise)
staffs = BaseRepository(Staff)

_VALID_LEVELS = {"low", "medium", "high"}
_VALID_STATUSES = {"open", "rectifying", "closed"}
_STATUS_FLOW = {
    "open": {"rectifying"},       # open 只能 → rectifying（派整改）
    "rectifying": {"closed"},     # rectifying 只能 → closed（完成整改）
    "closed": set(),              # closed 终态
}


class InspectionError(ValueError):
    """巡查台账操作不合规。"""


def _validate_level(level: str) -> None:
    if level not in _VALID_LEVELS:
        raise InspectionError(f"隐患等级无效: {level}（应为 low/medium/high）")


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise InspectionError(f"状态无效: {status}（应为 open/rectifying/closed）")


async def _generate_batch(inspection_date: Optional[date]) -> str:
    """生成巡查单号 INS-YYYYMMDD-NNN（当日自增 3 位）。"""
    d = inspection_date or date.today()
    prefix = f"INS-{d.strftime('%Y%m%d')}"
    stmt = select(InspectionRecord.inspection_batch).where(
        InspectionRecord.inspection_batch.like(f"{prefix}%")
    ).distinct()
    from app.db.database import get_session_factory
    async with get_session_factory()() as s:
        rows = (await s.execute(stmt)).scalars().all()
    nums = []
    pat = re.compile(rf"^{re.escape(prefix)}-(\d{{3}})$")
    for b in rows:
        m = pat.match(b or "")
        if m:
            nums.append(int(m.group(1)))
    seq = max(nums, default=0) + 1
    return f"{prefix}-{seq:03d}"


async def create_record(
    *,
    enterprise_id: int,
    inspection_date: date,
    inspector_user_id: Optional[int] = None,
    inspectors: Optional[str] = None,
    title: str = "",
    location: str = "",
    hazard_level: str = "low",
    hazard_category: Optional[str] = None,
    hazard_description: str = "",
    rect_responsible_user_id: Optional[int] = None,
    rect_responsible_name: Optional[str] = None,
    rect_deadline: Optional[date] = None,
    inspection_batch: Optional[str] = None,
    hazards: Optional[list[dict]] = None,
) -> list[InspectionRecord]:
    """新建巡查记录（支持一次多条隐患）。

    hazards 语义（新建巡查单弹窗的多隐患录入）：
      - 传入非空列表 → 每项 {hazard_level, hazard_category, hazard_description}
        生成一条记录，同批次同企业 hazard_index 1..N，hazard_count_total = N；
      - 不传/为空 → 兼容旧单条入参（hazard_level/hazard_category/hazard_description）。

    巡查人语义：inspectors（顿号分隔的原文，可自由键入）与
    inspector_user_id（可选关联员工档案）至少提供一个。

    整改责任人语义：rect_responsible_user_id（员工档案）与
    rect_responsible_name（企业安全员/负责人等纯文本姓名）二选一，
    二者均空则记录保持待处理状态。

    inspection_batch 语义：
      - 不传 → 生成新批次号，表示一次新的巡查；
      - 传入 → 复用已有批次，表示加入同一次巡查（多条隐患共享一个批次）。
      复用前校验批次存在。
    """
    entries = list(hazards) if hazards else [{
        "hazard_level": hazard_level,
        "hazard_category": hazard_category,
        "hazard_description": hazard_description,
    }]
    for i, h in enumerate(entries, 1):
        lv = h.get("hazard_level") or "low"
        _validate_level(lv)
        h["hazard_level"] = lv
        if not (h.get("hazard_description") or "").strip():
            raise InspectionError(f"第 {i} 条隐患描述不能为空")
    if not (inspectors and inspectors.strip()) and not inspector_user_id:
        raise InspectionError("巡查人不能为空")

    # 校验外键存在
    ent = await enterprises.get(enterprise_id)
    if not ent:
        raise InspectionError(f"企业不存在: id={enterprise_id}")
    if inspector_user_id is not None:
        insp = await staffs.get(inspector_user_id)
        if not insp:
            raise InspectionError(f"巡查人不存在: id={inspector_user_id}")
    if rect_responsible_user_id is not None:
        resp = await staffs.get(rect_responsible_user_id)
        if not resp:
            raise InspectionError(f"整改责任人不存在: id={rect_responsible_user_id}")

    # 批次：传入则复用（必须已存在），不传则新建
    if inspection_batch:
        existing = await records.get_by(inspection_batch=inspection_batch)
        if not existing:
            raise InspectionError(f"巡查批次不存在: {inspection_batch}")
        batch = inspection_batch
    else:
        batch = await _generate_batch(inspection_date)

    total = len(entries)
    created: list[InspectionRecord] = []
    for idx, h in enumerate(entries, 1):
        created.append(await records.create(
            inspection_batch=batch,
            enterprise_id=enterprise_id,
            title=(title or "").strip() or None,
            inspection_date=inspection_date,
            location=location or None,
            inspector_user_id=inspector_user_id,
            inspectors=(inspectors or "").strip() or None,
            hazard_index=idx if total > 1 else None,
            hazard_count_total=total if total > 1 else None,
            hazard_level=h["hazard_level"],
            hazard_category=(h.get("hazard_category") or None),
            hazard_description=h["hazard_description"].strip(),
            rect_responsible_user_id=rect_responsible_user_id,
            rect_responsible_name=(rect_responsible_name or "").strip() or None,
            rect_deadline=rect_deadline,
            rect_completed_at=None,
            status=(
                "rectifying"
                if (rect_responsible_user_id or (rect_responsible_name or "").strip())
                else "open"
            ),
        ))
    return created


async def list_batches() -> list[dict]:
    """已有巡查批次列表（按批次聚合，供新建时选择"加入同一次巡查"）。

    返回 [{batch, title, inspection_date, hazard_count}]，按 inspection_date 倒序。
    """
    from collections import defaultdict
    from app.db.database import get_session_factory

    stmt = select(InspectionRecord).order_by(InspectionRecord.inspection_date.desc())
    items, _ = await records.list(stmt=stmt, page=1, page_size=5000)

    groups: dict[str, list[InspectionRecord]] = defaultdict(list)
    for r in items:
        groups[r.inspection_batch].append(r)

    result = []
    for batch, rows in groups.items():
        first = rows[0]
        result.append({
            "batch": batch,
            "title": first.title,
            "inspection_date": str(first.inspection_date) if first.inspection_date else None,
            "hazard_count": len(rows),
        })
    return result


async def list_records(
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    enterprise_id: Optional[int] = None,
    inspection_batch: Optional[str] = None,
) -> tuple[list[dict], int]:
    """分页列表 + 关键词(标题/隐患描述) + 状态/企业/批次筛选。

    返回的每条记录附带 enterprise_name / inspector_name / rect_responsible_name，
    供前端直接展示（避免前端二次查询）。
    """
    from datetime import date as _date

    stmt = select(InspectionRecord).outerjoin(
        Enterprise, Enterprise.id == InspectionRecord.enterprise_id
    )
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            InspectionRecord.title.like(kw),
            InspectionRecord.hazard_description.like(kw),
            Enterprise.name.like(kw),
            Enterprise.code.like(kw),
        ))
    if status:
        if status == "overdue":
            # GET ?status=overdue 便捷查询：未闭环 + 整改期限已过
            today = _date.today()
            stmt = stmt.where(
                InspectionRecord.status != "closed",
                InspectionRecord.rect_deadline.isnot(None),
                InspectionRecord.rect_deadline < today,
            )
        else:
            _validate_status(status)
            stmt = stmt.where(InspectionRecord.status == status)
    if enterprise_id:
        stmt = stmt.where(InspectionRecord.enterprise_id == enterprise_id)
    if inspection_batch:
        stmt = stmt.where(InspectionRecord.inspection_batch == inspection_batch)

    items, total = await records.list(
        stmt=stmt, page=page, page_size=page_size,
        order_by=[InspectionRecord.hazard_index, InspectionRecord.id],
    )
    enriched = [await _enrich(r) for r in items]
    return enriched, total


async def list_overdue(*, limit: int = 20) -> list[dict]:
    """超期未闭环隐患列表（rect_deadline < 今天且 status != closed），按期限升序。"""
    from datetime import date as _date

    today = _date.today()
    stmt = (
        select(InspectionRecord)
        .where(
            InspectionRecord.status != "closed",
            InspectionRecord.rect_deadline.isnot(None),
            InspectionRecord.rect_deadline < today,
        )
        .order_by(InspectionRecord.rect_deadline.asc())
        .limit(limit)
    )
    items, _ = await records.list(stmt=stmt, page=1, page_size=limit)
    return [await _enrich(r) for r in items]


async def _enrich(r: InspectionRecord) -> dict:
    """给记录附加外键展示名 —— 纯 Enterprise/Staff 主表 JOIN，无快照回退。

    FK 关联率 100%（2026-10-16 数据清洗后），不再回退 InspectionRecord 冗余列。
    """
    d = r.dict()
    if r.enterprise_id:
        ent = await enterprises.get(r.enterprise_id)
        d["enterprise_name"] = ent.name if ent else None
        d["enterprise_code"] = ent.code if ent else None
        d["enterprise_region"] = ent.region if ent else None
        d["enterprise_industry"] = ent.industry if ent else None
    else:
        d["enterprise_name"] = None
        d["enterprise_code"] = None
        d["enterprise_region"] = None
        d["enterprise_industry"] = None
    # 巡查人展示：优先用导入/录入的检查人原文（顿号分隔多人），
    # 无原文时再回退关联员工档案姓名
    if r.inspectors:
        d["inspector_name"] = r.inspectors
    elif r.inspector_user_id:
        u = await staffs.get(r.inspector_user_id)
        d["inspector_name"] = u.name if u else None
    else:
        d["inspector_name"] = None
    if r.rect_responsible_user_id:
        u = await staffs.get(r.rect_responsible_user_id)
        d["rect_responsible_name"] = u.name if u else (r.rect_responsible_name or None)
    else:
        d["rect_responsible_name"] = r.rect_responsible_name or None
    return d


async def get_record(record_id: int) -> Optional[dict]:
    """按 id 查单条（带外键名）。"""
    r = await records.get(record_id)
    if not r:
        return None
    return await _enrich(r)


async def update_record(
    record_id: int,
    *,
    title: Optional[str] = None,
    location: Optional[str] = None,
    inspection_date: Optional[date] = None,
    inspection_batch: Optional[str] = None,
    inspector_user_id: Optional[int] = None,
    inspectors: Optional[str] = None,
    hazard_level: Optional[str] = None,
    hazard_category: Optional[str] = None,
    hazard_description: Optional[str] = None,
    rect_responsible_user_id: Optional[int] = None,
    rect_responsible_name: Optional[str] = None,
    rect_deadline: Optional[date] = None,
    review_date: Optional[date] = None,
    rect_completed_at: Optional[date] = None,
    notice_number: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[dict]:
    """更新记录字段。包含状态流转校验。

    状态流转规则：open→rectifying（派整改）、rectifying→closed（完成整改）。
    closed 是终态，不可改回。

    完成整改（status=closed）时：
      - rect_completed_at 不传则取 datetime.now()
      - review_date 自动取整改完成时间的日期（完成时间写入复查时间）
    """
    r = await records.get(record_id)
    if not r:
        return None

    updates: dict = {}
    if title is not None:
        if not title.strip():
            raise InspectionError("标题不能为空")
        updates["title"] = title.strip()
    if location is not None:
        updates["location"] = location or None
    if inspection_date is not None:
        updates["inspection_date"] = inspection_date
    if inspection_batch is not None:
        updates["inspection_batch"] = inspection_batch or None
    if inspector_user_id is not None:
        if not await staffs.get(inspector_user_id):
            raise InspectionError(f"巡查人不存在: id={inspector_user_id}")
        updates["inspector_user_id"] = inspector_user_id
    if inspectors is not None:
        updates["inspectors"] = inspectors or None
    if hazard_level is not None:
        _validate_level(hazard_level)
        updates["hazard_level"] = hazard_level
    if hazard_category is not None:
        updates["hazard_category"] = hazard_category or None
    if hazard_description is not None:
        if not hazard_description.strip():
            raise InspectionError("隐患描述不能为空")
        updates["hazard_description"] = hazard_description.strip()
    if rect_responsible_user_id is not None:
        if not await staffs.get(rect_responsible_user_id):
            raise InspectionError(f"整改责任人不存在: id={rect_responsible_user_id}")
        updates["rect_responsible_user_id"] = rect_responsible_user_id
    if rect_responsible_name is not None:
        # 纯文本责任人路径：未同时指定员工外键时，清掉旧外键（支持改成/改回
        # 企业安全员等非员工档案责任人）
        updates["rect_responsible_name"] = rect_responsible_name or None
        if rect_responsible_user_id is None:
            updates["rect_responsible_user_id"] = None
    if rect_deadline is not None:
        updates["rect_deadline"] = rect_deadline
    if notice_number is not None:
        # 空白串归一为 None；同批次同步在记录更新后统一执行
        updates["notice_number"] = notice_number.strip() or None

    # 状态流转
    if status is not None:
        _validate_status(status)
        allowed = _STATUS_FLOW.get(r.status, set())
        if status not in allowed and status != r.status:
            raise InspectionError(
                f"非法状态流转: {r.status} → {status}"
            )
        updates["status"] = status
        if status == "closed":
            # 整改完成时间：前端传则用（date → 当天 00:00:00），不传则取当前
            if rect_completed_at is not None:
                completed_dt = datetime.combine(
                    rect_completed_at, datetime.min.time()
                )
            else:
                completed_dt = datetime.now()
            updates["rect_completed_at"] = completed_dt
            # 复查日期：自动取整改完成时间的日期（完成时间写入复查时间）
            updates["review_date"] = (
                review_date if review_date is not None else completed_dt.date()
            )
        elif status == "rectifying":
            # 派整改时可同步设复查/完成时间（一般留空）
            if review_date is not None:
                updates["review_date"] = review_date
            if rect_completed_at is not None:
                updates["rect_completed_at"] = rect_completed_at
    else:
        # 不改状态也可单独更新这两个字段
        if review_date is not None:
            updates["review_date"] = review_date
        if rect_completed_at is not None:
            updates["rect_completed_at"] = rect_completed_at

    if not updates:
        return await _enrich(r)

    # 用户主动清空的字段（空串 → None）需显式置 NULL（repo 默认跳过 None）：
    # 覆盖 location/inspectors/hazard_category/notice_number/
    # rect_responsible_name / rect_responsible_user_id（责任人切换与清空）
    set_null_fields = {k for k, v in updates.items() if v is None}

    updated = await records.update(
        record_id, set_null=set_null_fields, **updates
    )

    # 编号同批次共享：本次键入了编号时，同步写入同批次其他所有记录
    # （批次本身若被修改，则同步到新批次）
    if notice_number is not None:
        target_batch = updates.get("inspection_batch", r.inspection_batch)
        async with records.session_scope() as s:
            await s.execute(
                update(InspectionRecord)
                .where(
                    InspectionRecord.inspection_batch == target_batch,
                    InspectionRecord.id != record_id,
                )
                .values(notice_number=updates["notice_number"])
            )

    return await _enrich(updated) if updated else None


async def delete_record(record_id: int) -> bool:
    """删除单条隐患记录（巡查台账允许删除已闭环的记录，未闭环的提示）。"""
    r = await records.get(record_id)
    if not r:
        return False
    if r.status != "closed":
        raise InspectionError("只有已闭环的记录才能删除")
    return await records.delete(record_id)


async def get_stats() -> dict:
    """巡查台账统计：总数、各状态数、逾期数、隐患等级分布。"""
    items, _ = await records.list(stmt=select(InspectionRecord), page=1, page_size=5000)
    now = date.today()
    open_count = rectifying_count = closed_count = overdue_count = 0
    low = medium = high = 0
    for r in items:
        if r.status == "open":
            open_count += 1
        elif r.status == "rectifying":
            rectifying_count += 1
            if r.rect_deadline and r.rect_deadline < now:
                overdue_count += 1
        elif r.status == "closed":
            closed_count += 1
        if r.hazard_level == "low":
            low += 1
        elif r.hazard_level == "medium":
            medium += 1
        elif r.hazard_level == "high":
            high += 1
    return {
        "total": len(items),
        "open_count": open_count,
        "rectifying_count": rectifying_count,
        "closed_count": closed_count,
        "overdue_count": overdue_count,
        "hazard_level_distribution": {"low": low, "medium": medium, "high": high},
    }


# ── 通用复合筛选（filter_service）─────────────────────────────
# 操作符常量已抽到 app.services.svc_Common.filter_helpers（4 表共用）。

INSPECTION_FILTER_FIELDS: dict[str, FieldSpec] = {
    # 虚拟关键词：企业主表 name/code + 隐患描述/检查人/责任人/批次 多字段 OR
    "keyword": FieldSpec(
        key="keyword", label="关键词", type="keyword",
        operators=["like", "like_not"], is_virtual=True,
        virtual_columns=[
            "enterprise_name", "hazard_description", "inspectors",
            "rect_responsible_name", "inspection_batch", "remark",
        ],
    ),
    # ── 文本列 ──
    "inspection_batch": FieldSpec(key="inspection_batch", label="检查批次", type="text", operators=TEXT_OPS),
    "enterprise_name": FieldSpec(key="enterprise_name", label="企业", type="text", operators=TEXT_OPS),
    "hazard_description": FieldSpec(key="hazard_description", label="隐患描述", type="text", operators=TEXT_OPS),
    "hazard_category": FieldSpec(key="hazard_category", label="隐患类别", type="text", operators=TEXT_OPS),
    "region": FieldSpec(key="region", label="园区", type="text", operators=TEXT_OPS),
    "industry": FieldSpec(key="industry", label="行业", type="text", operators=TEXT_OPS),
    "inspectors": FieldSpec(key="inspectors", label="检查人", type="text", operators=TEXT_OPS),
    "rect_responsible_name": FieldSpec(key="rect_responsible_name", label="整改责任人", type="text", operators=TEXT_OPS),
    "remark": FieldSpec(key="remark", label="备注", type="text", operators=TEXT_OPS),
    # ── 枚举列 ──
    "status": FieldSpec(
        key="status", label="状态", type="enum", operators=ENUM_OPS,
        options=["open", "rectifying", "closed", "overdue"],
    ),
    "hazard_level": FieldSpec(
        key="hazard_level", label="隐患等级", type="enum", operators=ENUM_OPS,
        options=["low", "medium", "high"],
    ),
    # ── 日期列 ──
    "inspection_date": FieldSpec(key="inspection_date", label="检查日期", type="date", operators=DATE_OPS),
    "rect_deadline": FieldSpec(key="rect_deadline", label="整改期限", type="date", operators=DATE_OPS),
    "review_date": FieldSpec(key="review_date", label="复查时间", type="date", operators=DATE_OPS),
    # ── 数字列 ──
    "hazard_index": FieldSpec(key="hazard_index", label="隐患序号", type="number", operators=NUM_OPS),
    "hazard_count_total": FieldSpec(key="hazard_count_total", label="隐患总数", type="number", operators=NUM_OPS),
    "enterprise_id": FieldSpec(key="enterprise_id", label="企业ID", type="number", operators=["eq", "neq", "is_null", "is_not_null"]),
}

# 日期字段与 coerce 逻辑已抽到 app.services.svc_Common.filter_helpers
# （date_field_keys 由 list_records_with_filter 调用时传入）。

# ── 企业列统一口径 ───────────────────────────────────────────
# 数据源锁死 Enterprise 主表（FK 关联率 100%，冗余列已删除）。
_INSPECTION_JOINS = [
    (Enterprise, Enterprise.id == InspectionRecord.enterprise_id, True),
]
_INSPECTION_COLUMN_OVERRIDES = {
    "enterprise_name": lambda: Enterprise.name,
}


def get_inspection_filter_fields() -> list[FieldSpec]:
    """巡查表筛选字段元数据（GET /inspection-records/meta/filter-fields 用）。"""
    ordered_keys = [
        "keyword",
        "enterprise_name", "inspection_batch",
        "hazard_description", "hazard_category", "region", "industry",
        "inspectors", "rect_responsible_name", "remark",
        "status", "hazard_level",
        "inspection_date", "rect_deadline", "review_date",
        "hazard_index", "hazard_count_total", "enterprise_id",
    ]
    return [INSPECTION_FILTER_FIELDS[k] for k in ordered_keys]


async def list_records_with_filter(
    filter_root,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """复合条件分页查询（POST /inspection-records/query 用）。

    排序按 检查日期倒序 → 企业名 → 隐患序号，保证同企业同批次行连续
    （前端合并单元格依赖）。

    日期规整 + 查询 + enrich 由 `filter_helpers.list_with_filter_wrapper`
    统一封装；本表特化的外键补名逻辑在 `_enrich`。

    业务特化：status=="overdue" 不是数据库实际值，是"未闭环 + 整改期限已过"
    的派生条件。这里先递归扫描 filter 树，把所有 status=="overdue" 的
    FilterLeaf 翻译成 AND(status != "closed", rect_deadline < 今天) 的 FilterGroup。
    """
    from datetime import date as _date

    today = _date.today()

    def _rewrite(node):
        """递归把 filter dict / Pydantic 实例里含 status=="overdue" 的叶子翻译成真正的超期条件组。

        支持两种入参形态：
        - raw dict（router 层 filter 还没 model_validate）
        - Pydantic FilterLeaf / FilterGroup 实例（router 已经 model_validate 过）

        前端 enum 筛选会把勾选值包成数组用 operator="in"，
        这里同时处理 eq（单值 overdue）和 in（数组含 overdue）两种形态。
        """
        # ── 1. dict 形态 ──
        if isinstance(node, dict):
            if node.get("type") == "leaf" and node.get("field") == "status":
                op = node.get("operator", "eq")
                val = node.get("value")
                contains_overdue = (
                    (op == "eq" and val == "overdue")
                    or (op == "in" and isinstance(val, list) and "overdue" in val)
                )
                if contains_overdue:
                    return {
                        "type": "group", "logic": "AND",
                        "children": [
                            {"type": "leaf", "field": "status", "operator": "neq", "value": "closed"},
                            {"type": "leaf", "field": "rect_deadline", "operator": "lt", "value": str(today)},
                        ],
                    }
            if node.get("type") == "group":
                return {**node, "children": [_rewrite(c) for c in node.get("children", [])]}
            return node

        # ── 2. Pydantic FilterLeaf / FilterGroup 形态 ──
        try:
            from app.services.svc_Common.filter_service import FilterLeaf as _FL, FilterGroup as _FG
        except Exception:
            return node

        if isinstance(node, _FL):
            if node.field == "status":
                contains_overdue = (
                    (node.operator == "eq" and node.value == "overdue")
                    or (node.operator == "in" and isinstance(node.value, list) and "overdue" in node.value)
                )
                if contains_overdue:
                    return _FG(
                        type="group", logic="AND",
                        children=[
                            _FL(field="status", operator="neq", value="closed"),
                            _FL(field="rect_deadline", operator="lt", value=str(today)),
                        ],
                    )
            return node
        if isinstance(node, _FG):
            return _FG(
                type="group", logic=node.logic,
                children=[_rewrite(c) for c in node.children],
            )
        return node

    filter_root = _rewrite(filter_root)

    # 兜底：前端 filter builder 始终传 group，但若有人直接用 leaf 调用，
    # 这里包一层 FilterGroup，避免 filter_service.list_with_filter 期望 group
    # 却收到 leaf 实例。
    try:
        from app.services.svc_Common.filter_service import FilterLeaf as _FL, FilterGroup as _FG
        if isinstance(filter_root, _FL):
            filter_root = _FG(type="group", logic="AND", children=[filter_root])
    except Exception:
        pass

    return await list_with_filter_wrapper(
        InspectionRecord,
        records,
        filter_root,
        INSPECTION_FILTER_FIELDS,
        page=page,
        page_size=page_size,
        order_by=[
            InspectionRecord.inspection_date.desc().nullslast(),
            _INSPECTION_COLUMN_OVERRIDES["enterprise_name"](),
            InspectionRecord.hazard_index,
            InspectionRecord.id,
        ],
        enrich_fn=_enrich,
        date_field_keys={"inspection_date", "rect_deadline", "review_date"},
        joins=_INSPECTION_JOINS,
        column_overrides=_INSPECTION_COLUMN_OVERRIDES,
    )
