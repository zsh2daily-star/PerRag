"""对话配额服务 —— 非管理员每日费用上限 + 管理员手动锁定。

用量来源：chat_message 表按 user_id（JOIN chat_session）聚合当日 assistant
消息的 token，乘以 config 的单价。只统计 assistant 消息——user 消息不消耗
上游模型 API token。

闸门位置：chat.send_message 入口处，admin 直接放行 → chat_locked 403 →
累计费用 ≥ 限额 403。
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import func, select
from sqlmodel import select as sm_select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import ChatMessage, ChatSession, Staff


async def get_daily_usage_yuan(
    user_id: int, d: Optional[date] = None
) -> float:
    """聚合指定日期（默认今天）该用户的 assistant 消息费用（元）。

    只算 assistant 消息的 token × 单价。user 消息不消耗上游 API。
    注意：费用只在 SSE 流结束后落库，所以当日最后一条消息的费用
    会在下一次检查时被计入——用户最后一条可能略微超额，可接受。
    """
    d = d or date.today()
    day_start = datetime.combine(d, time.min)
    day_end = datetime.combine(d, time.max)

    stmt = (
        select(
            func.coalesce(func.sum(ChatMessage.prompt_tokens), 0),
            func.coalesce(func.sum(ChatMessage.completion_tokens), 0),
        )
        .select_from(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatSession.user_id == user_id,
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= day_start,
            ChatMessage.created_at <= day_end,
        )
    )
    async with get_session_factory()() as db:
        row = (await db.execute(stmt)).one()
    prompt_total, completion_total = row

    cost = (
        (prompt_total or 0) / 1_000_000 * settings.chat_price_input_per_m
        + (completion_total or 0) / 1_000_000 * settings.chat_price_output_per_m
    )
    return round(cost, 6)


async def get_user_daily_limit(staff: Staff) -> Optional[float]:
    """返回某用户的每日限额（元）。None 表示无限制。

    管理员始终无限制；普通用户优先取 chat_daily_limit_yuan，None 则用系统默认。
    """
    if staff.is_admin:
        return None  # 管理员不受限
    if staff.chat_daily_limit_yuan is not None:
        return float(staff.chat_daily_limit_yuan)
    return settings.chat_daily_limit_default


async def check_chat_allowed(
    staff: Staff,
) -> tuple[bool, Optional[str], Optional[float], Optional[float]]:
    """三级闸门检查。返回 (允许, 原因, 今日已用, 限额)。

    允许=True → 原因/今日已用/限额 均为 None
    允许=False → 原因是人类可读的拒绝文案
    管理员直接放行（is_admin=True）
    """
    # 1. 管理员直接放行
    if staff.is_admin:
        return True, None, None, None

    # 2. 管理员手动锁定
    if staff.chat_locked:
        return False, "对话功能已被管理员锁定，请联系管理员解锁", None, None

    # 3. 每日费用限额
    limit = await get_user_daily_limit(staff)
    usage = await get_daily_usage_yuan(staff.id)
    if usage >= limit:
        return (
            False,
            f"今日用量已达上限 ¥{usage:.2f} / ¥{limit:.2f}，明日零点重置",
            usage,
            limit,
        )
    return True, None, usage, limit


async def get_chat_usage_info(staff: Staff) -> dict:
    """前端 /chat/usage 的响应体。"""
    allowed, reason, usage, limit = await check_chat_allowed(staff)
    if staff.is_admin:
        return {
            "allowed": True,
            "reason": None,
            "usage_yuan": 0.0,
            "limit_yuan": None,
            "remaining_yuan": None,
            "locked": False,
        }
    # 非管理员
    limit_display = (
        limit if staff.chat_daily_limit_yuan is not None
        else settings.chat_daily_limit_default
    )
    return {
        "allowed": allowed,
        "reason": reason,
        "usage_yuan": round(usage or 0, 4),
        "limit_yuan": limit_display,
        "remaining_yuan": round(max(0, (limit or 0) - (usage or 0)), 4),
        "locked": staff.chat_locked,
    }
