"""编程式执行 Alembic 迁移 —— 供应用启动时调用（替代 SQLModel.metadata.create_all）。

在独立线程里跑 alembic command：env.py 内部使用 asyncio.run，
必须放在没有运行中事件循环的线程中执行。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _run_upgrade() -> None:
    """同步执行 alembic upgrade head（在工作线程内调用）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    """启动时调用：把数据库升级到最新版本。

    - 全新库：执行全部迁移（基线建 5 表）；
    - 已 stamp 的库：无操作；
    - 落后的库：增量升级，数据保留。
    """
    await asyncio.get_event_loop().run_in_executor(None, _run_upgrade)
    logger.info("Alembic 迁移已到最新版本")
