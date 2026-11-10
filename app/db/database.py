"""数据库引擎与会话管理 —— 基于 SQLModel 的异步 PostgreSQL 访问层。

提供：
- engine：从 settings.pg_dsn 创建的异步引擎
- async_session_factory：生成 AsyncSession 的工厂
- get_db()：FastAPI 依赖，每个请求 yield 一个 AsyncSession
- init_db()：启动时执行 Alembic 迁移到 head（见 app/db/migrate.py，不再使用 create_all）
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings

logger = logging.getLogger(__name__)

# 异步引擎：从配置读取 DSN 与连接池大小
# pool_size 控制与 PostgreSQL 之间保持的常驻连接数
engine = create_async_engine(
    settings.pg_dsn,
    pool_size=settings.pg_pool_size,
    echo=False,
)

# 异步会话工厂：每次调用生成一个新的 AsyncSession
# expire_on_commit=False 避免 commit 后访问属性触发额外查询（异步上下文里会报错）
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_session_factory() -> async_sessionmaker:
    """返回异步会话工厂。

    供非 FastAPI 依赖注入场景（如启动时种子数据初始化）使用。
    """
    return async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求获取一个 AsyncSession，请求结束自动关闭。

    用法（在路由函数签名中）：
        @app.get("/items")
        async def list_items(session: AsyncSession = Depends(get_db)):
            ...

    async with 确保请求处理完后 session.close() 被调用，
    即使路由抛异常也会执行上下文清理。
    """
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """启动时迁移数据库到最新版本（Alembic 版本化迁移）。

    取代旧的 SQLModel.metadata.create_all：
    - 全新库执行基线迁移建表；已存在的库增量升级，不丢数据。
    - create_all 无法给已存在的表加字段，Alembic 解决了这个问题。
    """
    from app.db.migrate import run_migrations

    await run_migrations()
    logger.info("数据库迁移完成")
