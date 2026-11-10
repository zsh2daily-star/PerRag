"""数据库种子数据 —— 初始化默认管理员账号。

V2 架构下不再有角色体系，只种一个 admin staff 账号。
在 init_db() 之后调用，幂等（已存在则跳过）。
"""
import logging
from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlmodel import select

from app.db.database import get_session_factory
from app.db.models import Staff

logger = logging.getLogger(__name__)
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def seed_database():
    """初始化管理员账号（幂等）。

    默认账号: admin / admin123（is_admin=true），首次启动后请及时修改密码。
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        admin = (await session.execute(
            select(Staff).where(Staff.username == "admin")
        )).scalars().first()

        if not admin:
            admin = Staff(
                username="admin",
                password_hash=_pwd_context.hash("admin123"),
                is_active=True,
                is_admin=True,
                name="系统管理员",
                department="监管中心",
                position="系统管理员",
            )
            session.add(admin)
            logger.info("创建管理员账号: admin / admin123（请及时修改密码）")

        await session.commit()
        logger.info("种子数据初始化完成")
