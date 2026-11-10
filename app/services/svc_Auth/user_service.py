"""账号服务 —— Staff CRUD、密码哈希校验、权限范围计算。

V2 架构: staff 表合一（凭证 + 档案 + is_admin），无角色体系。
权限模型: is_admin 布尔位仅额外放行账号管理接口；业务数据全员平等。

本模块基于 BaseRepository（app/db/crud.py）做存取，本文件只写业务规则。
"""
import logging
import re
import secrets
from datetime import datetime

from passlib.context import CryptContext
from sqlmodel import select, or_

from app.db.crud import BaseRepository
from app.db.models import Staff

logger = logging.getLogger(__name__)

# repo 单例：本文件所有函数共用
staffs = BaseRepository(Staff)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 生成随机初始密码的字符集（去除易混淆字符 I/O/0/1）
_UPPER = "ABCDEFGHJKMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_PASSWORD_LEN = 10  # 8-18 位，取 10 保底

# 密码规则:8-18 位，数字/字母/符号 至少两种组合（与登录页 REGEXP_PWD 对齐）
_PWD_PATTERN = re.compile(
    r"^(?![0-9]+$)(?![a-z]+$)(?![A-Z]+$)(?!([^(0-9a-zA-Z)]|[()])+$)"
    r"(?!^.*[\u4E00-\u9FA5].*$)"
    r"([^(0-9a-zA-Z)]|[()]|[a-z]|[A-Z]|[0-9]){8,18}$"
)


class PasswordPolicyError(ValueError):
    """密码不符合规则（8-18 位 + 至少两种类型组合）。"""


def validate_password(password: str) -> None:
    """校验密码规则:8-18 位，数字/字母/符号至少两种组合。不合规抛 PasswordPolicyError。"""
    if not password:
        raise PasswordPolicyError("密码不能为空")
    if not _PWD_PATTERN.match(password):
        raise PasswordPolicyError("密码需 8-18 位，含数字/字母/符号至少两种组合")


def hash_password(password: str) -> str:
    """密码哈希（bcrypt）。"""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。"""
    return _pwd_context.verify(plain, hashed)


def generate_initial_password() -> str:
    """生成随机初始密码:保证必含大写字母 + 数字两种类型，10 位，去除易混淆字符。

    策略:先从大写字母和数字各取 1 位保底（满足两种类型），
    其余 8 位从两者并集中随机取，最后打乱顺序。
    """
    pool = _UPPER + _DIGITS
    chars = [
        secrets.choice(_UPPER),   # 至少 1 个大写字母
        secrets.choice(_DIGITS),  # 至少 1 个数字
    ] + [secrets.choice(pool) for _ in range(_PASSWORD_LEN - 2)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


async def authenticate(username: str, password: str) -> Staff | None:
    """用户名+密码认证，返回 Staff 或 None。成功时更新 last_login_at。"""
    staff = await staffs.get_by(username=username)
    if not staff or not staff.is_active:
        return None
    if not verify_password(password, staff.password_hash):
        return None
    # 更新最近登录时间（naive datetime，匹配 TIMESTAMP WITHOUT TIME ZONE）
    return await staffs.update(staff.id, last_login_at=datetime.now())


async def get_staff_by_id(staff_id: int) -> Staff | None:
    """按 id 查 staff。"""
    return await staffs.get(staff_id)


async def change_password(staff_id: int, old_password: str, new_password: str) -> bool:
    """用户自行修改密码:校验旧密码后写入新密码。

    返回 True 表示修改成功, False 表示旧密码不匹配或账号不存在。
    新密码需符合规则,否则抛 PasswordPolicyError（由调用方处理）。
    """
    # 校验新密码规则:8-18 位 + 至少两种类型组合
    validate_password(new_password)
    if new_password == old_password:
        raise PasswordPolicyError("新密码不能与旧密码相同")

    staff = await staffs.get(staff_id)
    if not staff:
        return False
    if not verify_password(old_password, staff.password_hash):
        return False
    await staffs.update(staff_id, password_hash=hash_password(new_password))
    return True


async def list_staff(
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Staff], int]:
    """分页查询 staff 列表。

    keyword 模糊匹配 username/name/department。
    返回 (items, total)。
    """
    stmt = select(Staff)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(
            Staff.username.like(like),
            Staff.name.like(like),
            Staff.department.like(like),
        ))
    return await staffs.list(stmt=stmt, page=page, page_size=page_size,
                             order_by=[Staff.id])


async def list_active_staff_brief() -> list[dict]:
    """获取启用中的 staff 简要列表（id + name），用于下拉选择器。"""
    items = await staffs.filter(is_active=True)
    return [{"id": s.id, "name": s.name} for s in items]


async def create_staff(
    username: str,
    name: str,
    department: str = "",
    position: str = "",
    phone: str = "",
    is_admin: bool = False,
) -> tuple[Staff, str]:
    """创建 staff 账号，返回 (staff, initial_password)。

    用户名唯一约束由 DB 保证，冲突时抛 IntegrityError。
    """
    password = generate_initial_password()
    staff = await staffs.create(
        username=username,
        password_hash=hash_password(password),
        is_active=True,
        is_admin=is_admin,
        name=name,
        department=department or None,
        position=position or None,
        phone=phone or None,
    )
    return staff, password


async def update_staff(staff_id: int, **fields) -> Staff | None:
    """更新 staff 档案字段（name/department/position/phone/is_admin）。

    不允许通过此方法改 username/password/is_active。
    """
    allowed = {"name", "department", "position", "phone", "is_admin"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return await get_staff_by_id(staff_id)
    return await staffs.update(staff_id, **updates)


async def reset_staff_password(staff_id: int) -> str | None:
    """重置 staff 密码为随机密码，返回新密码。staff 不存在返回 None。"""
    staff = await staffs.get(staff_id)
    if not staff:
        return None
    password = generate_initial_password()
    await staffs.update(staff_id, password_hash=hash_password(password))
    return password


async def set_staff_active(staff_id: int, is_active: bool) -> Staff | None:
    """启用/停用 staff 账号。staff 不存在返回 None。"""
    return await staffs.update(staff_id, is_active=is_active)


async def count_active_admins() -> int:
    """统计启用中的管理员数量（停用最后一个管理员时用）。"""
    return await staffs.count(is_admin=True, is_active=True)


def compute_scope(staff: Staff | None) -> dict:
    """计算请求权限范围。

    V2 权限模型: 业务数据全员平等（后端不按企业过滤），
    is_admin 仅额外放行账号管理接口。
    返回: {"is_admin": bool, "allowed_enterprises": "all"}
    """
    if not staff:
        return {"is_admin": False, "allowed_enterprises": []}
    return {"is_admin": staff.is_admin, "allowed_enterprises": "all"}
