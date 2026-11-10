"""4 表 service 共享样板：操作符常量、日期值规整、查询包装。

之前 enterprise_service / inspection_service / factory_in_factory_service
各自重复定义 `_TEXT_OPS` 等常量、`_coerce_date_values`、
`list_xxx_with_filter` 包装函数，风格还不一致。本模块抽出统一来源，
各 service 只保留 FieldSpec 配置 + 可选的 `_enrich`（外键补名/时间格式化
是各表特化逻辑，不通用化）。

- `coerce_date_values`：把日期字段 leaf 的字符串值就地转成 date，参数化查询需要
- `list_with_filter_wrapper`：包装 `filter_service.list_with_filter`，
  封装「日期规整 → 查询 → enrich」三步流程
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Optional, Sequence, Union

from app.db.crud import BaseRepository
from app.services.svc_Common.filter_service import (
    ColumnOverride,
    FieldSpec,
    FilterError,
    FilterGroup,
    FilterLeaf,
    JoinSpec,
    list_with_filter,
)

# ── 操作符常量（统一来源，去 `_` 前缀）────────────────────────────
# 由 enterprise_service / inspection_service / factory_in_factory_service
# 的 FieldSpec 引用，避免每表各定义一份漂移。
TEXT_OPS = ["like", "like_not", "eq", "neq", "is_null", "is_not_null"]
ENUM_OPS = ["eq", "neq", "in", "not_in", "is_null", "is_not_null"]
DATE_OPS = ["eq", "neq", "gt", "lt", "gte", "lte", "is_null", "is_not_null"]
NUM_OPS = ["eq", "neq", "gt", "lt", "gte", "lte", "is_null", "is_not_null"]
BOOL_OPS = ["is_true", "is_false", "is_null", "is_not_null"]


def coerce_date_values(
    node: Union[FilterGroup, FilterLeaf, None],
    date_field_keys: set[str],
) -> None:
    """把筛选树中日期字段 leaf 的字符串值就地转成 date。

    前端传字符串，后端 SQL 参数化查询需要 date 类型。
    `date_field_keys` 由调用方按本表特化的日期列集合传入（如
    factory_in_factory 只有 inspection_date；inspection 还有 rect_deadline）。
    """
    if node is None or not date_field_keys:
        return
    if isinstance(node, FilterLeaf):
        if (
            node.field in date_field_keys
            and isinstance(node.value, str)
            and node.value
        ):
            try:
                node.value = date.fromisoformat(node.value)
            except ValueError:
                raise FilterError(
                    f"日期格式无效: {node.value}（应为 YYYY-MM-DD）"
                )
        return
    for child in node.children:
        coerce_date_values(child, date_field_keys)


# enrich_fn 签名：model 实例 → dict（外键补名 + 时间格式化等表特化逻辑）
EnrichFn = Callable[[Any], "Any"]  # async (model) -> dict | model


async def list_with_filter_wrapper(
    model,
    repo: BaseRepository,
    filter_root: Union[FilterGroup, FilterLeaf, None],
    allowed_fields: dict[str, FieldSpec],
    *,
    page: int = 1,
    page_size: int = 20,
    order_by: Optional[Sequence] = None,
    enrich_fn: Optional[EnrichFn] = None,
    date_field_keys: Optional[set[str]] = None,
    joins: Optional[Sequence[JoinSpec]] = None,
    column_overrides: Optional[dict[str, ColumnOverride]] = None,
) -> tuple[list, int]:
    """包装 `filter_service.list_with_filter`，封装 3 步流程。

    1. 若 `date_field_keys` 非空 → 调 `coerce_date_values` 规整日期值
    2. 调 `list_with_filter` 查询分页
    3. 若 `enrich_fn` 提供 → 对每条记录 enrich 后返回 list[dict]；
       否则原样返回 list[model]（适配 enterprise 无 enrich 场景）

    enrich_fn 是 async (model) -> dict；wrapper 不做类型断言，
    调用方自保返回类型一致。
    """
    if date_field_keys and isinstance(
        filter_root, (FilterGroup, FilterLeaf)
    ):
        coerce_date_values(filter_root, date_field_keys)
    items, total = await list_with_filter(
        model,
        repo,
        filter_root,
        allowed_fields,
        page=page,
        page_size=page_size,
        order_by=order_by,
        joins=joins,
        column_overrides=column_overrides,
    )
    if enrich_fn is not None:
        enriched = [await enrich_fn(r) for r in items]
        return enriched, total
    return items, total
