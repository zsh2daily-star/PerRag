"""工作台统计服务 —— 聚合 inspection_record + material + enterprise + staff。

返回 dashboard 首页的统计数据：
  - open_count: 未闭环隐患数
  - overdue_count: 超期未闭环数
  - monthly_new: 本月新增巡查记录数
  - material_total: 材料总数
  - hazard_level_distribution: {low, medium, high}
  - recent_materials: 最近上传材料（前 5 条）
  - overdue_list: 超期隐患列表（前 10 条）
"""
import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlmodel import select

from app.db.crud import BaseRepository
from app.db.models import Enterprise, InspectionRecord, Material, Staff

inspection_records = BaseRepository(InspectionRecord)
materials = BaseRepository(Material)
enterprises = BaseRepository(Enterprise)
staffs = BaseRepository(Staff)


async def get_dashboard_stats() -> dict:
    """聚合工作台统计数据。"""
    today = date.today()
    month_start = today.replace(day=1)

    # --- inspection_record 统计 ---
    from app.db.database import get_session_factory
    session_factory = get_session_factory()
    async with session_factory() as session:
        # 未闭环数
        open_count = (await session.execute(
            select(func.count(InspectionRecord.id)).where(InspectionRecord.status != "closed")
        )).scalar() or 0

        # 超期数
        overdue_count = (await session.execute(
            select(func.count(InspectionRecord.id)).where(
                InspectionRecord.status != "closed",
                InspectionRecord.rect_deadline.isnot(None),
                InspectionRecord.rect_deadline < today,
            )
        )).scalar() or 0

        # 本月新增
        monthly_new = (await session.execute(
            select(func.count(InspectionRecord.id)).where(
                InspectionRecord.inspection_date >= month_start
            )
        )).scalar() or 0

        # 等级分布
        level_rows = (await session.execute(
            select(InspectionRecord.hazard_level, func.count(InspectionRecord.id))
            .group_by(InspectionRecord.hazard_level)
        )).all()
        level_distribution = {"low": 0, "medium": 0, "high": 0}
        for level, cnt in level_rows:
            if level in level_distribution:
                level_distribution[level] = cnt

        # 超期列表（前 10 条，按 deadline 升序：最紧急的在前）
        overdue_rows = (await session.execute(
            select(InspectionRecord)
            .where(
                InspectionRecord.status != "closed",
                InspectionRecord.rect_deadline.isnot(None),
                InspectionRecord.rect_deadline < today,
            )
            .order_by(InspectionRecord.rect_deadline.asc())
            .limit(10)
        )).scalars().all()

        # 材料总数
        material_total = (await session.execute(
            select(func.count(Material.id))
        )).scalar() or 0

        # 最近材料（前 5 条）
        recent_material_rows = (await session.execute(
            select(Material).order_by(Material.created_at.desc()).limit(5)
        )).scalars().all()

    # 超期列表 enrich（企业名 + 责任人名）
    overdue_list = []
    enterprise_cache = {}
    staff_cache = {}
    for r in overdue_rows:
        ent_name = enterprise_cache.get(r.enterprise_id)
        if ent_name is None:
            ent = await enterprises.get(r.enterprise_id)
            ent_name = ent.name if ent else "未知企业"
            enterprise_cache[r.enterprise_id] = ent_name
        resp_name = staff_cache.get(r.rect_responsible_user_id) if r.rect_responsible_user_id else None
        if resp_name is None and r.rect_responsible_user_id:
            s = await staffs.get(r.rect_responsible_user_id)
            resp_name = s.name if s else None
            staff_cache[r.rect_responsible_user_id] = resp_name
        overdue_list.append({
            "id": r.id,
            "enterprise_name": ent_name,
            "title": r.title,
            "hazard_level": r.hazard_level,
            "hazard_category": r.hazard_category,
            "rect_deadline": str(r.rect_deadline) if r.rect_deadline else None,
            "rect_responsible_name": resp_name,
            "status": r.status,
        })

    # 最近材料 enrich（上传人姓名 + enterprise_codes 解析）
    recent_materials = []
    for m in recent_material_rows:
        uploader_name = None
        if m.uploaded_by:
            u = staff_cache.get(m.uploaded_by)
            if u is None:
                s = await staffs.get(m.uploaded_by)
                u = s.name if s else None
                staff_cache[m.uploaded_by] = u
            uploader_name = u
        try:
            codes = json.loads(m.enterprise_codes) if m.enterprise_codes else []
        except (json.JSONDecodeError, TypeError):
            codes = []
        recent_materials.append({
            "uid": m.uid,
            "filename": m.filename,
            "file_format": m.source_format,
            "status": m.status,
            "uploaded_by_name": uploader_name,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })

    return {
        "open_count": open_count,
        "overdue_count": overdue_count,
        "monthly_new": monthly_new,
        "material_total": material_total,
        "hazard_level_distribution": level_distribution,
        "recent_materials": recent_materials,
        "overdue_list": overdue_list,
    }
