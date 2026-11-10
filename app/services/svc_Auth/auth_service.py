"""认证服务 —— JWT 签发与校验。

V2 架构 JWT claim 结构:
    {"sub": str(staff_id), "username": str, "name": str,
     "is_admin": bool, "exp": datetime, "type": "access"}

JWT 自带 is_admin/name，避免每次请求都查库即可填充 request.state.user。
后端账号管理接口仍以 is_admin 守卫，前端按 is_admin 控菜单。
"""
import jwt
from datetime import datetime, timedelta, timezone

from app.config import settings

_ALGORITHM = "HS256"


def create_access_token(
    staff_id: int,
    username: str,
    name: str = "",
    is_admin: bool = False,
) -> str:
    """签发 JWT access token。

    将 is_admin/name 写入 claim，中间件解码后可直接拿到，无需查库。
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(staff_id),
        "username": username,
        "name": name,
        "is_admin": is_admin,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """解码并校验 JWT。返回 claim dict 或 None（无效/过期）。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
