"""Alembic 迁移环境（异步 asyncpg）。

- 数据库 URL 从环境变量 PG_DSN 读取（与 app.config 一致）；
- target_metadata 用 SQLModel.metadata，导入 app.db.models 确保 5 张表全部注册；
- 支持离线（--sql）与在线两种模式。
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from sqlmodel import SQLModel

# 让 env.py 在任何工作目录下都能 import 到 app 包
# 本文件路径：<工作目录>/app/alembic/env.py，app 包在其上两级
APP_PARENT = Path(__file__).resolve().parents[2]
if str(APP_PARENT) not in sys.path:
    sys.path.insert(0, str(APP_PARENT))

# 导入全部模型，填充 SQLModel.metadata
from app.db import models  # noqa: E402,F401

config = context.config

# 从环境变量注入异步 DSN（alembic.ini 里故意留空，不写死口令）
_pg_dsn = os.getenv(
    "PG_DSN",
    "postgresql+asyncpg://rag:rag_secret@localhost:5432/rag",
)
config.set_main_option("sqlalchemy.url", _pg_dsn)

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # alembic.ini 缺失/为空时 fileConfig 会抛 RuntimeError，
        # 不能让日志配置问题阻断迁移本身（用默认日志即可）
        import logging
        logging.getLogger("alembic").warning(
            "alembic.ini 日志配置加载失败，已跳过 fileConfig: %s",
            config.config_file_name,
        )

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：异步引擎连接数据库执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
