"""认证端点（V2）：登录 / 当前用户 / 修改密码。"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求。"""
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """修改密码请求 —— 用户自行修改，需校验旧密码。"""
    old_password: str
    new_password: str


@router.post("/login")
async def login(body: LoginRequest):
    """用户名+密码登录 → 返回 JWT + 用户信息。

    响应对齐前端契约: {success, data: {accessToken, refreshToken, expires,
    username, nickname, is_admin, roles, permissions}}。
    is_admin → roles=['admin'] 否则 ['staff']（前端按 meta.roles 过滤菜单）。
    """
    from app.services.svc_Auth.auth_service import create_access_token, decode_token
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Auth.user_service import authenticate

    staff = await authenticate(body.username, body.password)
    if not staff:
        # 登录失败必记审计（决策三）
        log_action_async(
            action="auth.login",
            resource_id=body.username,
            status="failed",
            error_message="用户名或密码错误",
            ip_address=None,
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(
        staff.id, staff.username, staff.name, staff.is_admin
    )
    # 从 token 解出 exp 作为 expires 返回前端
    claim = decode_token(token) or {}
    exp_ts = claim.get("exp")
    from datetime import datetime, timezone
    expires = (
        datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
        if exp_ts else "2030/10/30 00:00:00"
    )

    roles = ["admin"] if staff.is_admin else ["staff"]
    permissions = ["*:*:*"] if staff.is_admin else []

    # 登录成功记审计
    log_action_async(
        action="auth.login",
        staff_id=staff.id,
        status="success",
        ip_address=None,
    )

    return {
        "success": True,
        "data": {
            "avatar": "",
            "username": staff.username,
            "nickname": staff.name,
            "is_admin": staff.is_admin,
            "roles": roles,
            "permissions": permissions,
            "accessToken": token,
            "refreshToken": token,  # V2 暂无 refresh 端点，占位
            "expires": expires,
        },
    }


@router.get("/me")
async def get_current_user(request: Request):
    """获取当前登录用户信息（需 JWT）。

    前端登录后/刷新页面时调用，用于恢复会话状态。
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    return {
        "success": True,
        "data": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "is_admin": getattr(user, "is_admin", False),
            "roles": ["admin"] if getattr(user, "is_admin", False) else ["staff"],
            "permissions": ["*:*:*"] if getattr(user, "is_admin", False) else [],
        },
    }


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request):
    """用户自行修改密码:输入旧密码 + 新密码。

    任何登录用户可用（非管理员也能改自己的密码）。
    旧密码错误返回 400；忘记密码需联系管理员重置。
    """
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Auth.user_service import change_password

    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或 token 无效")

    try:
        ok = await change_password(user.id, body.old_password, body.new_password)
    except ValueError as e:
        # 密码规则不合规（PasswordPolicyError 也继承 ValueError）
        raise HTTPException(status_code=400, detail=str(e))

    if not ok:
        # 旧密码错误记审计（可疑操作追踪）
        log_action_async(
            action="auth.change_password",
            staff_id=user.id,
            status="failed",
            error_message="旧密码不匹配",
        )
        raise HTTPException(status_code=400, detail="旧密码错误")

    log_action_async(
        action="auth.change_password",
        staff_id=user.id,
        status="success",
    )
    return {"success": True, "message": "密码修改成功"}
