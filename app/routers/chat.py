"""智能问答端点 —— 会话/消息 CRUD + 到 Hermes Agent API 的 SSE 流式代理。

架构：
    浏览器 ──平台 JWT──> rag-api /chat/... ──Bearer HERMES_API_KEY──> hermes-webui:8642
    HERMES_API_KEY 只存在于后端，浏览器不直接接触 Hermes。

Hermes API 是无状态的：每次请求都要带全量对话，多轮上下文由本服务
从 chat_session / chat_message 组装后转发。SSE 字节原样透传
（包括 event: hermes.tool.progress 工具进度帧），同时旁路累积
assistant 文本，流结束（或客户端中途断开）后落库为 assistant 消息。
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import ChatMessage, ChatSession, Staff
from app.services.svc_Auth.chat_quota_service import check_chat_allowed, get_chat_usage_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["智能问答"])

HISTORY_LIMIT = 100          # 转发给 Hermes 的最大历史消息条数
DEFAULT_TITLE = "新对话"
UPSTREAM_URL = f"{settings.hermes_api_base}/v1/chat/completions"

# agent 工具执行可能很久没有字节产出，read timeout 从宽；connect 要短
_UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)


# ── 请求模型 ──────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class SendMessageRequest(BaseModel):
    content: str


# ── 序列化 / 属主校验 ─────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _session_out(s: ChatSession) -> dict:
    return {
        "id": str(s.id),
        "title": s.title,
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }


def _message_out(m: ChatMessage) -> dict:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "total_tokens": m.total_tokens,
        "created_at": _iso(m.created_at),
    }


def _require_user(request: Request):
    """chat 是登录用户功能：无平台 JWT（API-Key/开发放行模式）一律 401。"""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    return user


async def _load_owned_session(session_id: str, user_id: int) -> ChatSession:
    """按 UUID 加载会话并校验属主；格式错/不存在/他人会话统一 404（不暴露存在性）。"""
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="会话不存在")
    async with get_session_factory()() as db:
        s = await db.get(ChatSession, sid)
        if not s or s.user_id != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")
        db.expunge(s)
        return s


# ── 会话 CRUD ─────────────────────────────────────────────

@router.post("/sessions")
async def create_session(body: CreateSessionRequest, request: Request):
    """新建问答会话，默认标题"新对话"（首条消息后自动改为消息前 20 字）。"""
    user = _require_user(request)
    title = (body.title or "").strip() or DEFAULT_TITLE
    async with get_session_factory()() as db:
        s = ChatSession(user_id=user.id, title=title[:200])
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return {"success": True, "data": _session_out(s)}


@router.get("/sessions")
async def list_sessions(request: Request):
    """列出当前用户的全部会话（updated_at 倒序）。"""
    user = _require_user(request)
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
            )
        ).scalars().all()
        return {"success": True, "data": [_session_out(s) for s in rows]}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """删除会话（消息随外键 ON DELETE CASCADE 一并删除）。"""
    from app.services.svc_Auth.audit_service import log_action_async

    user = _require_user(request)
    s = await _load_owned_session(session_id, user.id)
    async with get_session_factory()() as db:
        await db.delete(s)
        await db.commit()
    log_action_async(
        action="chat.session.delete",
        staff_id=user.id,
        resource_id=session_id,
    )
    return {"success": True}


@router.get("/pricing")
async def get_pricing(request: Request):
    """问答计费单价（人民币 / 百万 tokens）—— 前端据此把 token 换算为费用。"""
    _require_user(request)
    return {
        "success": True,
        "data": {
            "currency": "CNY",
            "unit": "per_million_tokens",
            "input": settings.chat_price_input_per_m,
            "output": settings.chat_price_output_per_m,
        },
    }


@router.get("/usage")
async def get_usage(request: Request):
    """当前用户的对话用量 —— 前端聊天页展示「今日剩余 / 限额」和超限/锁定状态。"""
    user = _require_user(request)
    async with get_session_factory()() as db:
        staff = await db.get(Staff, user.id)
    if not staff:
        raise HTTPException(status_code=404, detail="用户不存在")
    info = await get_chat_usage_info(staff)
    return {"success": True, "data": info}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str, request: Request):
    """拉取会话历史消息（正序返回，最多最新 100 条）。"""
    user = _require_user(request)
    await _load_owned_session(session_id, user.id)
    async with get_session_factory()() as db:
        # 倒序取最新 N 条，再反转为时间正序给前端
        rows = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == uuid.UUID(session_id))
                .order_by(ChatMessage.created_at.desc())
                .limit(HISTORY_LIMIT)
            )
        ).scalars().all()
        return {"success": True, "data": [_message_out(m) for m in reversed(rows)]}


# ── 流式发消息（核心）─────────────────────────────────────

async def _persist_after_stream(
    session_id: uuid.UUID,
    text: str,
    *,
    usage: Optional[dict] = None,
    touch: bool = True,
) -> None:
    """流结束后落库 assistant 消息（含 token 用量）并刷新会话时间。

    独立 DB session（generator 生命周期可能超出请求注入 session）。
    客户端断连后在 generator 关闭流程中调用，加超时保护避免挂死；
    任何失败只记日志，不影响连接关闭。
    """
    if not text.strip():
        return
    usage = usage or {}
    try:
        async with get_session_factory()() as db:
            db.add(ChatMessage(
                session_id=session_id,
                role="assistant",
                content=text,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ))
            if touch:
                s = await db.get(ChatSession, session_id)
                if s:
                    s.updated_at = datetime.now()
            await db.commit()
    except Exception:
        logger.exception("assistant 消息落库失败 session=%s", session_id)


async def _retitle_and_user_message(
    db, session: ChatSession, content: str
) -> None:
    """落 user 消息；会话仍是默认标题时用首条消息前 20 字改标题。"""
    db.add(ChatMessage(session_id=session.id, role="user", content=content))
    if session.title == DEFAULT_TITLE:
        session.title = content.strip()[:20] or DEFAULT_TITLE
    session.updated_at = datetime.now()
    await db.commit()


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: SendMessageRequest, request: Request):
    """保存 user 消息 → 组装历史 → 流式转发 Hermes → 透传 SSE → 落库 assistant。

    响应是 Hermes 原生 SSE（text/event-stream），前端直接消费：
      - 无 event 行的 data 帧 = OpenAI chat.completion.chunk（文本 delta）
      - event: hermes.tool.progress = 工具调用进度（实时展示，不落库）
      - 末尾 chunk 带 usage = token 用量（透传给前端 + 旁路落库）
      - data: [DONE] = 结束
    """
    from app.services.svc_Auth.audit_service import log_action_async

    user = _require_user(request)
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session = await _load_owned_session(session_id, user.id)

    # ── 对话配额闸门 ──
    async with get_session_factory()() as _db:
        staff_obj = await _db.get(Staff, user.id)
    if staff_obj:
        allowed, reason, _, _ = await check_chat_allowed(staff_obj)
        if not allowed:
            raise HTTPException(status_code=403, detail=reason)

    if not settings.hermes_api_key:
        raise HTTPException(status_code=503, detail="问答服务未配置 HERMES_API_KEY")

    # 1. 落 user 消息 + 改标题
    async with get_session_factory()() as db:
        bound = await db.get(ChatSession, session.id)
        await _retitle_and_user_message(db, bound, content)

    # 2. 组装转发给 Hermes 的历史（含刚落库的本条 user 消息，正序）
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(ChatMessage.role, ChatMessage.content)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(HISTORY_LIMIT)
            )
        ).all()
    messages = [{"role": r, "content": c} for r, c in reversed(rows)]

    # 3. 先建立上游连接：此时响应头未发，建连/鉴权失败可以直接返回 JSON 错误
    client = httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT)
    try:
        upstream_cm = client.stream(
            "POST",
            UPSTREAM_URL,
            content=json.dumps(
                {
                    "model": "hermes-agent",
                    "messages": messages,
                    "stream": True,
                },
                ensure_ascii=False,
            ),
            headers={
                "Authorization": f"Bearer {settings.hermes_api_key}",
                "Content-Type": "application/json",
            },
        )
        upstream = await upstream_cm.__aenter__()
    except httpx.HTTPError as e:
        await client.aclose()
        logger.warning("连接 Hermes 失败: %s", e)
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    if upstream.status_code >= 400:
        err_body = (await upstream.aread())[:500]
        await upstream_cm.__aexit__(None, None, None)
        await client.aclose()
        logger.warning("Hermes 返回 %s: %s", upstream.status_code, err_body[:200])
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "detail": f"问答服务返回错误（{upstream.status_code}）",
            },
        )

    # 4. 透传 + 旁路累积
    async def event_stream():
        accumulated = ""
        pending = b""
        usage: Optional[dict] = None
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk  # 原样透传（保留 event: 行）

                # 按 SSE 空行分帧，跨 chunk 的半包留在 pending
                pending += chunk
                *frames, pending = pending.split(b"\n\n")
                for frame in frames:
                    for line in frame.splitlines():
                        if not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == b"[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        # 上游错误帧：不累积、不抛断流，前端据 data.error 提示
                        if isinstance(obj, dict) and obj.get("error"):
                            continue
                        # usage 帧（流末尾的最后一个 chunk 带用量）：捕获后落库
                        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                            usage = obj["usage"]
                            continue
                        try:
                            delta = obj["choices"][0].get("delta", {}).get("content")
                        except (KeyError, IndexError, AttributeError, TypeError):
                            delta = None
                        if isinstance(delta, str) and delta:
                            accumulated += delta
        except (httpx.HTTPError, asyncio.CancelledError, GeneratorExit):
            pass  # 上游中断 / 用户点停止：已有文本照常落库
        finally:
            try:
                await upstream_cm.__aexit__(None, None, None)
            finally:
                await client.aclose()
            # GeneratorExit/Cancelled 下用 shield + 短超时保住落库
            try:
                await asyncio.wait_for(
                    asyncio.shield(
                        _persist_after_stream(session.id, accumulated, usage=usage)
                    ),
                    timeout=15,
                )
            except Exception:
                logger.exception("流式收尾失败 session=%s", session.id)

    log_action_async(
        action="chat.message.send",
        staff_id=user.id,
        resource_id=str(session.id),
    )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
