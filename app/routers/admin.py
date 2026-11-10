"""账号管理端点（仅 is_admin=true）：staff CRUD + 审计日志。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["账号管理"])


def _require_admin(request: Request):
    """FastAPI 依赖: 校验当前登录用户是否管理员，否则 403。

    V2 权限模型: is_admin 布尔位仅额外放行账号管理接口。
    """
    user = getattr(request.state, "user", None)
    if not user or not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _fmt_dt(dt) -> str | None:
    """datetime → 'YYYY-MM-DD HH:MM:SS'，None 保持 None。"""
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt, "strftime") else str(dt)


def _staff_to_dict(staff) -> dict:
    """Staff ORM → 前端期望的 dict（时间格式化 + 对话配额）。"""
    return {
        "id": staff.id,
        "username": staff.username,
        "name": staff.name,
        "department": staff.department or "",
        "position": staff.position or "",
        "phone": staff.phone or "",
        "is_admin": staff.is_admin,
        "is_active": staff.is_active,
        "last_login_at": _fmt_dt(staff.last_login_at),
        "created_at": _fmt_dt(staff.created_at),
        "chat_daily_limit_yuan": staff.chat_daily_limit_yuan,
        "chat_locked": staff.chat_locked,
    }


class CreateStaffRequest(BaseModel):
    """创建账号请求。"""
    username: str
    name: str
    department: str = ""
    position: str = ""
    phone: str = ""
    is_admin: bool = False


class UpdateStaffRequest(BaseModel):
    """更新账号档案请求（不允许改 username/password/is_active）。"""
    name: str | None = None
    department: str | None = None
    position: str | None = None
    phone: str | None = None
    is_admin: bool | None = None


@router.get("/staff")
async def admin_list_staff(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    _admin=Depends(_require_admin),
):
    """账号列表（分页 + 关键词模糊匹配 username/name/department）。"""
    from app.services.svc_Auth.user_service import list_staff

    items, total = await list_staff(keyword=keyword, page=page, page_size=page_size)
    return {
        "success": True,
        "data": {"total": total, "items": [_staff_to_dict(s) for s in items]},
    }


@router.get("/staff/brief")
async def admin_staff_brief(request: Request, _admin=Depends(_require_admin)):
    """启用中的账号简要列表（id + name），用于下拉选择器。"""
    from app.services.svc_Auth.user_service import list_active_staff_brief

    return {"success": True, "data": await list_active_staff_brief()}


@router.get("/audit-logs")
async def admin_audit_logs(
    page: int = 1,
    page_size: int = 20,
    action: str = "",
    staff_id: str = "",
    start_date: str = "",
    end_date: str = "",
    _admin=Depends(_require_admin),
):
    """审计日志列表（分页 + action/staff_id/日期范围筛选，仅管理员）。"""
    from datetime import date as _date
    from app.services.svc_Auth.audit_service import list_audit_logs

    sd = _date.fromisoformat(start_date) if start_date else None
    ed = _date.fromisoformat(end_date) if end_date else None
    sid = int(staff_id) if staff_id else None
    items, total = await list_audit_logs(
        page=page, page_size=page_size,
        action=action or None,
        staff_id=sid,
        start_date=sd, end_date=ed,
    )
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/audit-logs/actions")
async def admin_audit_actions(_admin=Depends(_require_admin)):
    """审计日志已有 action 列表（去重，供前端筛选下拉）。"""
    from app.services.svc_Auth.audit_service import list_audit_actions
    return {"success": True, "data": await list_audit_actions()}


@router.post("/staff")
async def admin_create_staff(
    body: CreateStaffRequest,
    request: Request,
    _admin=Depends(_require_admin),
):
    """创建账号，返回初始密码（仅展示一次）。"""
    from sqlalchemy.exc import IntegrityError
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Auth.user_service import create_staff

    try:
        staff, password = await create_staff(
            username=body.username,
            name=body.name,
            department=body.department,
            position=body.position,
            phone=body.phone,
            is_admin=body.is_admin,
        )
    except IntegrityError:
        raise HTTPException(status_code=400, detail="用户名已存在")

    log_action_async(
        action="admin.staff.create",
        staff_id=_admin.id,
        resource_type="staff",
        resource_id=str(staff.id),
        request_payload={"username": body.username, "is_admin": body.is_admin},
    )
    return {
        "success": True,
        "data": {"staff": _staff_to_dict(staff), "initial_password": password},
    }


@router.put("/staff/{staff_id}")
async def admin_update_staff(
    staff_id: int,
    body: UpdateStaffRequest,
    request: Request,
    _admin=Depends(_require_admin),
):
    """更新账号档案/管理员标记。"""
    from app.services.svc_Auth.user_service import update_staff

    staff = await update_staff(
        staff_id,
        name=body.name,
        department=body.department,
        position=body.position,
        phone=body.phone,
        is_admin=body.is_admin,
    )
    if not staff:
        return {"success": False, "data": None, "message": "员工不存在"}
    return {"success": True, "data": _staff_to_dict(staff)}


@router.post("/staff/{staff_id}/reset-password")
async def admin_reset_password(
    staff_id: int,
    request: Request,
    _admin=Depends(_require_admin),
):
    """重置密码为随机 8 位，返回新密码。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Auth.user_service import reset_staff_password

    password = await reset_staff_password(staff_id)
    if password is None:
        return {"success": False, "data": None, "message": "员工不存在"}
    log_action_async(
        action="admin.staff.reset_password",
        staff_id=_admin.id,
        resource_type="staff",
        resource_id=str(staff_id),
    )
    return {"success": True, "data": {"new_password": password}}


@router.post("/staff/{staff_id}/deactivate")
async def admin_toggle_staff_status(
    staff_id: int,
    request: Request,
    _admin=Depends(_require_admin),
):
    """切换账号启用状态（停用↔启用）。

    前端 toggleStatus 调此端点，后端按当前状态切换。
    守卫: 不能停用自己；不能停用最后一个启用中的管理员。
    """
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Auth.user_service import get_staff_by_id, set_staff_active, count_active_admins

    staff = await get_staff_by_id(staff_id)
    if not staff:
        return {"success": False, "message": "员工不存在"}

    # 不能停用自己
    if staff.id == _admin.id and staff.is_active:
        raise HTTPException(status_code=400, detail="不能停用当前登录账号")

    # 不能停用最后一个启用的管理员
    if staff.is_active and staff.is_admin:
        active_admins = await count_active_admins()
        if active_admins <= 1:
            raise HTTPException(
                status_code=400,
                detail="不能停用最后一个启用的管理员",
            )

    new_active = not staff.is_active
    updated = await set_staff_active(staff_id, new_active)
    log_action_async(
        action="admin.staff.deactivate" if not new_active else "admin.staff.activate",
        staff_id=_admin.id,
        resource_type="staff",
        resource_id=str(staff_id),
    )
    action = "停用" if not new_active else "启用"
    return {
        "success": True,
        "data": _staff_to_dict(updated),
        "message": f"已{action}",
    }


class ChatSettingsRequest(BaseModel):
    """对话配置更新（可部分更新）。"""
    chat_daily_limit_yuan: float | None = None  # None=清除个人限额，回退系统默认；负数不允许
    chat_locked: bool | None = None


@router.put("/staff/{staff_id}/chat-settings")
async def admin_update_chat_settings(
    staff_id: int,
    body: ChatSettingsRequest,
    request: Request,
    _admin=Depends(_require_admin),
):
    """管理员调某用户的对话限额 / 锁定状态。"""
    from app.db.database import get_session_factory
    from app.db.models import Staff

    async with get_session_factory()() as db:
        staff = await db.get(Staff, staff_id)
        if not staff:
            raise HTTPException(status_code=404, detail="用户不存在")
        if body.chat_daily_limit_yuan is not None:
            if body.chat_daily_limit_yuan < 0:
                raise HTTPException(status_code=400, detail="限额不能为负数")
            staff.chat_daily_limit_yuan = body.chat_daily_limit_yuan
        if body.chat_locked is not None:
            staff.chat_locked = body.chat_locked
        await db.commit()
        await db.refresh(staff)

    return {"success": True, "data": _staff_to_dict(staff)}
