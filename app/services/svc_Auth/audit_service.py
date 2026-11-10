"""审计服务 —— 记录每次 API 调用的审计日志。

写入策略:
    1. 中间件层自动记录所有非公开路径的请求（status, latency）
    2. 业务层在关键操作时显式调用 log_action() 补充业务语义
    3. 异步写入（asyncio.create_task），不阻塞响应

本模块基于 BaseRepository（app/db/crud.py）做存取。
"""
import asyncio
import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import and_
from sqlmodel import select

from app.db.crud import BaseRepository
from app.db.models import AuditLog, Staff

logger = logging.getLogger(__name__)

# repo 单例
audit_logs = BaseRepository(AuditLog)
staffs = BaseRepository(Staff)


async def log_action(
    action: str,
    staff_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_payload: dict | None = None,
    ip_address: str | None = None,
    latency_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """写入一条审计日志（异步，不阻塞调用方）。

    V2: 用 staff_id（凭证档案合一后不再区分 account/user）。
    决策三: 只记写操作 + 登录事件，读请求一律不记。
    """
    try:
        await audit_logs.create(
            staff_id=staff_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_payload=json.dumps(request_payload, ensure_ascii=False)[:4000] if request_payload else None,
            ip_address=ip_address,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )
    except Exception as e:
        # 审计日志写入失败不应影响业务
        logger.warning("审计日志写入失败: %s — %s", action, e)


def log_action_async(**kwargs) -> None:
    """异步写入审计日志（fire and forget，不阻塞调用方）。

    用法:
        log_action_async(action="material.upload", staff_id=1, ...)
    """
    asyncio.create_task(log_action(**kwargs))


async def list_audit_logs(
    *,
    page: int = 1,
    page_size: int = 20,
    action: Optional[str] = None,
    staff_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> tuple[list[dict], int]:
    """分页查询审计日志（支持 action/staff_id/日期范围筛选）。

    返回的每条记录附带 staff_name，字段名对齐前端契约：
      - ip_address → ip
      - request_payload → request_summary
    """
    from app.db.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = select(AuditLog)
        conditions = []
        if action:
            conditions.append(AuditLog.action == action)
        if staff_id is not None:
            conditions.append(AuditLog.staff_id == staff_id)
        if start_date:
            conditions.append(AuditLog.created_at >= start_date)
        if end_date:
            # 截止日期包含当天
            from datetime import timedelta
            conditions.append(AuditLog.created_at < end_date + timedelta(days=1))
        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(AuditLog.created_at.desc())

        # 计数
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        # 分页
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await session.execute(stmt)).scalars().all()

    # enrich：查 staff_name
    staff_cache = {}
    items = []
    for r in rows:
        staff_name = staff_cache.get(r.staff_id)
        if staff_name is None and r.staff_id is not None:
            s = await staffs.get(r.staff_id)
            staff_name = s.name if s else None
            staff_cache[r.staff_id] = staff_name
        items.append({
            "id": r.id,
            "staff_id": r.staff_id,
            "staff_name": staff_name,
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "status": r.status,
            "latency_ms": r.latency_ms,
            "ip": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "request_summary": r.request_payload,
            "error_message": r.error_message,
        })
    return items, total


async def list_audit_actions() -> list[str]:
    """返回审计日志中已有的 action 去重列表（供前端筛选下拉）。"""
    from app.db.database import get_session_factory
    from sqlalchemy import func

    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = (await session.execute(
            select(AuditLog.action).distinct().order_by(AuditLog.action)
        )).scalars().all()
    return list(rows)
