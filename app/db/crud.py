"""通用数据访问层（DAL / Repository）—— 消除 service 层的 session 样板。

提供 BaseRepository 泛型基类，每张表实例化一次后即可复用标准 CRUD：

    from app.db.crud import BaseRepository
    from app.db.models import Enterprise

    enterprises = BaseRepository(Enterprise)   # service 文件顶部实例化

    # 之后在 service 函数里：
    ent = await enterprises.get(1)
    ent = await enterprises.get_by(code="91310115MA1K3PQ7X9")
    ok  = await enterprises.exists(name="xxx")
    ent = await enterprises.create(name=..., code=...)
    ent = await enterprises.update(1, address="新地址")
    await enterprises.delete(1)
    items, total = await enterprises.list(page=1, page_size=10)

跨表事务用 session_scope() 共享同一 session：

    async with BaseRepository.session_scope() as s:
        ent = await enterprises.get(1, session=s)
        cnt = await inspections.count(session=s, enterprise_id=1)
        if cnt:
            raise ValueError("有关联台账，禁止删除")
        await enterprises.delete(1, session=s)

约定：
- 所有方法不抛业务异常；找不到返回 None / False / 空列表，由 service 判断。
- update 只更新显式传入的字段（None 值跳过），并自动刷新对象。
- list 返回 (items, total)，total 是不带分页的全量计数。
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional, Sequence, Type, TypeVar

from sqlalchemy import func as sa_func, inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

from app.db.database import get_session_factory

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SQLModel)


class BaseRepository:
    """单表通用 CRUD 仓库。

    实例化时绑定一个 SQLModel 表类；所有方法可选传入外部 session
    （跨表事务场景），不传则自开自关。
    """

    _model: Type[T]

    def __init__(self, model: Type[T]):
        self._model = model

    # ── session 管理 ──────────────────────────────────────────

    @staticmethod
    @asynccontextmanager
    async def session_scope() -> AsyncGenerator[AsyncSession, None]:
        """跨表事务上下文：同一 session 贯穿多个 repo 调用。

        commit 时机由调用方控制（退出 with 时自动 commit）；
        抛异常自动 rollback。
        """
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def _session(
        self, session: Optional[AsyncSession]
    ) -> AsyncGenerator[AsyncSession, None]:
        """内部用：有外部 session 就复用（不负责关闭/提交），没有就自开自关。"""
        if session is not None:
            yield session
        else:
            session_factory = get_session_factory()
            async with session_factory() as s:
                yield s

    # ── 读 ────────────────────────────────────────────────────

    async def get(
        self, pk: Any, *, session: Optional[AsyncSession] = None
    ) -> Optional[T]:
        """按主键查单条，找不到返回 None。"""
        async with self._session(session) as s:
            return await s.get(self._model, pk)

    async def get_by(
        self, *, session: Optional[AsyncSession] = None, **filters: Any
    ) -> Optional[T]:
        """按等值条件查单条，多条时取第一条，找不到返回 None。"""
        async with self._session(session) as s:
            stmt = select(self._model).filter_by(**filters)
            return (await s.execute(stmt)).scalars().first()

    async def filter(
        self,
        *,
        session: Optional[AsyncSession] = None,
        **filters: Any,
    ) -> list[T]:
        """按等值条件查列表（不分页）。"""
        async with self._session(session) as s:
            stmt = select(self._model).filter_by(**filters)
            return list((await s.execute(stmt)).scalars().all())

    async def exists(
        self, *, session: Optional[AsyncSession] = None, **filters: Any
    ) -> bool:
        """按等值条件判断存在性。"""
        async with self._session(session) as s:
            stmt = select(self._model).filter_by(**filters)
            return (await s.execute(stmt)).scalars().first() is not None

    async def count(
        self,
        *,
        session: Optional[AsyncSession] = None,
        stmt=None,
        **filters: Any,
    ) -> int:
        """计数。可传自定义 stmt（带 where 的 select）或等值 filters。"""
        async with self._session(session) as s:
            if stmt is not None:
                sub = stmt.subquery()
                c = await s.execute(select(sa_func.count()).select_from(sub))
            else:
                c = await s.execute(
                    select(sa_func.count())
                    .select_from(self._model)
                    .filter_by(**filters)
                )
            return c.scalar() or 0

    async def list(
        self,
        *,
        session: Optional[AsyncSession] = None,
        stmt=None,
        order_by: Optional[Sequence[Any]] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[T], int]:
        """分页列表。可传自定义 stmt（含 where 条件）或用默认全表。

        返回 (items, total)：total 是过滤后、不带分页的总数。
        order_by 传列引用列表，如 [Enterprise.id.desc()]。
        """
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        if page_size > 5000:
            page_size = 5000

        base = stmt if stmt is not None else select(self._model)

        async with self._session(session) as s:
            # total：不带分页/排序的计数
            sub = base.subquery()
            total = (await s.execute(
                select(sa_func.count()).select_from(sub)
            )).scalar() or 0

            q = base
            if order_by:
                q = q.order_by(*order_by)
            q = q.offset((page - 1) * page_size).limit(page_size)
            items = list((await s.execute(q)).scalars().all())
            return items, total

    # ── 写 ────────────────────────────────────────────────────

    async def create(
        self,
        *,
        session: Optional[AsyncSession] = None,
        commit: bool = True,
        **values: Any,
    ) -> T:
        """插入一行并刷新（拿回数据库生成的 id/默认值）。

        commit=False 时由调用方（session_scope/外层事务）统一提交。
        """
        obj = self._model(**values)
        async with self._session(session) as s:
            s.add(obj)
            if commit:
                await s.commit()
            await s.refresh(obj)
            return obj

    async def update(
        self,
        pk: Any,
        *,
        session: Optional[AsyncSession] = None,
        commit: bool = True,
        set_null: Optional[set[str]] = None,
        **values: Any,
    ) -> Optional[T]:
        """按主键更新显式传入的字段，找不到返回 None。

        - values 中 None 默认跳过（表示"未提供/不改"）；
        - set_null 中的字段显式置为 NULL：随 values 传入的允许 None 写入，
          未随 values 传入的（服务层仅声明清空）也单独置 NULL。
        """
        async with self._session(session) as s:
            obj = await s.get(self._model, pk)
            if not obj:
                return None
            changed = False
            for k, v in values.items():
                if v is None and (not set_null or k not in set_null):
                    continue
                setattr(obj, k, v)
                changed = True
            for k in set_null or ():
                if k in values:
                    continue
                setattr(obj, k, None)
                changed = True
            if changed:
                s.add(obj)
                if commit:
                    await s.commit()
                await s.refresh(obj)
            return obj

    async def delete(
        self,
        pk: Any,
        *,
        session: Optional[AsyncSession] = None,
        commit: bool = True,
    ) -> bool:
        """按主键删除，找不到返回 False。"""
        async with self._session(session) as s:
            obj = await s.get(self._model, pk)
            if not obj:
                return False
            await s.delete(obj)
            if commit:
                await s.commit()
            return True
