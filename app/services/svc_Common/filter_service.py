"""通用复合筛选条件拼装层 —— 与具体业务表解耦。

本模块提供三件事：
  1. Pydantic 模型（FilterLeaf/FilterGroup/FilterNode + FieldSpec）：前后端
     共享的 JSON 树结构（嵌套 AND/OR 组 + 叶子条件）。
  2. build_clause(model, node, allowed_fields)：把 FilterNode 递归翻译为
     SQLAlchemy 的 ClauseElement（and_/or_/==/ilike/in_ 等），空值 leaf
     返回 None（被父组过滤），白名单违规抛 ValueError（service 层转 422）。
  3. list_with_filter(model, repo, ...)：通用分页查询入口，调
     BaseRepository.list(stmt=...) 接受已构造的 select。

每张业务表（Enterprise/Material/InspectionRecord）在自己的 service 文件里
定义 `XXX_FILTER_FIELDS: dict[str, FieldSpec]` 和 `get_xxx_filter_fields()`
函数（返回 list 形式给 meta endpoint），然后调用 list_with_filter 完成查询。

虚拟字段（如 keyword）的处理：FieldSpec 标 is_virtual=True + virtual_columns=
[list[str]]，build_clause 见到 is_virtual=True 时走 or_(*[col.ilike(...)]) 多
字段 OR 路径，不调 getattr(model, field)。这样 filter_service 不耦合任何
具体业务字段名。
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, Optional, Sequence, Union

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, cast
from sqlalchemy.sql import ColumnElement
from sqlalchemy.types import ARRAY, Text
from sqlmodel import select

from app.db.crud import BaseRepository


# ── 操作符枚举 ──────────────────────────────────────────────
Operator = Literal[
    "eq", "neq", "like", "like_not",
    "in", "not_in",
    "gt", "lt", "gte", "lte",
    "is_true", "is_false",
    "is_null", "is_not_null",
    "contains", "overlaps",
]

# ── 字段类型枚举（前端渲染值控件的依据）─────────────────────
FieldType = Literal["enum", "text", "bool", "number", "date", "keyword", "enum_array"]


# ── 前后端共享的 FieldSpec（同时供 meta endpoint 与 service 白名单）──
class FieldSpec(BaseModel):
    """字段元数据：驱动前端 UI 渲染 + 后端白名单校验，单一来源。

    - key:        字段名（= SQLAlchemy 列属性名 / 虚拟字段名）
    - label:      界面显示名
    - type:       字段类型（决定值控件 + 允许的 operator 集合）
    - operators:  允许的 operator 列表（前端 UI 用，后端不强制校验）
    - options:    固定枚举值（内联，如 ["规上","规下"]）
    - options_source: 动态枚举值来源 endpoint（如 "/enterprises/meta/industries"）
    - is_virtual: 虚拟字段（如 keyword）不对应单一列，build_clause 走特殊路径
    - virtual_columns: 虚拟字段实际 OR 的列名列表（仅 is_virtual=True 时填）
    """
    key: str
    label: str
    type: FieldType
    operators: list[Operator]
    options: Optional[list[str]] = None
    options_source: Optional[str] = None
    is_virtual: bool = False
    virtual_columns: list[str] = Field(default_factory=list)


# ── FilterNode 树结构（Pydantic v2 discriminated union）──
class FilterLeaf(BaseModel):
    """叶子条件：field + operator + value。"""
    type: Literal["leaf"] = "leaf"
    field: str
    operator: Operator
    value: Any = None


class FilterGroup(BaseModel):
    """逻辑组：AND 或 OR + 子节点列表（可嵌套）。"""
    type: Literal["group"] = "group"
    logic: Literal["AND", "OR"] = "AND"
    children: list["FilterNode"] = Field(default_factory=list)


# discriminated union：通过 type 字段路由到 leaf/group
FilterNode = Annotated[
    Union[FilterLeaf, FilterGroup],
    Field(discriminator="type"),
]

# 让 FilterGroup.children 能引用 FilterNode（前向引用需要 rebuild）
FilterGroup.model_rebuild()


# ── 最大嵌套深度兜底（前端限 2 层，后端兜底 3 层防恶意请求）──
MAX_FILTER_DEPTH = 3


class FilterError(ValueError):
    """筛选条件不合规（白名单违规/深度超限/操作符不支持）。"""


# 列覆盖：返回替代 getattr(model, field) 的表达式（如外连接 COALESCE 补名）
ColumnOverride = Callable[[], ColumnElement]
# 连接规格：(target, onclause, isouter)
JoinSpec = tuple[Any, Any, bool]


# ── 翻译函数 ──────────────────────────────────────────────
def _is_empty_value(operator: Operator, value: Any) -> bool:
    """判断 leaf 是否应被跳过：value 为 None / 空串 / 空数组。

    is_null / is_not_null 不需要 value，不算空。
    """
    if operator in ("is_null", "is_not_null", "is_true", "is_false"):
        return False
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (list, tuple, set)) and len(value) == 0:
        return True
    return False


def _apply_operator(col, operator: Operator, value: Any) -> Optional[ColumnElement]:
    """对单一列应用 operator，返回 SQLAlchemy clause 或 None。

    col 是 SQLAlchemy InstrumentedAttribute（如 Enterprise.name）。
    """
    if operator == "eq":
        return col == value
    if operator == "neq":
        return col != value
    if operator == "like":
        if value is None or value == "":
            return None
        return col.ilike(f"%{value}%")
    if operator == "like_not":
        if value is None or value == "":
            return None
        return ~col.ilike(f"%{value}%")
    if operator == "in":
        if not value:
            return None
        return col.in_(value)
    if operator == "not_in":
        if not value:
            return None
        return ~col.in_(value)
    if operator == "gt":
        return col > value
    if operator == "lt":
        return col < value
    if operator == "gte":
        return col >= value
    if operator == "lte":
        return col <= value
    if operator == "is_true":
        return col.is_(True)
    if operator == "is_false":
        return col.is_(False)
    if operator == "is_null":
        return col.is_(None)
    if operator == "is_not_null":
        return col.isnot(None)
    # ── 数组专用操作符（PostgreSQL text[] / ARRAY）──
    # contains: col @> value（col 包含 value 的所有元素；value 必须是 list）
    # overlaps: col && value（col 与 value 有任一元素交集）
    if operator == "contains":
        if not value:
            return None
        if isinstance(value, str):
            value = [value]
        return col.op('@>')(cast(value, ARRAY(Text)))
    if operator == "overlaps":
        if not value:
            return None
        if isinstance(value, str):
            value = [value]
        return col.op('&&')(cast(value, ARRAY(Text)))
    return None


def build_clause(
    model,
    node: Union[FilterLeaf, FilterGroup],
    allowed_fields: dict[str, FieldSpec],
    *,
    depth: int = 0,
    column_overrides: Optional[dict[str, ColumnOverride]] = None,
) -> Optional[ColumnElement]:
    """递归把 FilterNode 翻译为 SQLAlchemy clause。

    - leaf：白名单校验 → getattr(model, field) → _apply_operator
    - group：递归 children → and_/or_，空 children 返回 None
    - 空值 leaf（如选了字段未填值）返回 None，父组用 [c for c in ... if c] 过滤
    - 白名单违规/操作符不支持/超深度：抛 FilterError（service 层转 422）
    """
    if depth > MAX_FILTER_DEPTH:
        raise FilterError(f"筛选条件嵌套深度超过 {MAX_FILTER_DEPTH} 层")

    if isinstance(node, FilterLeaf):
        spec = allowed_fields.get(node.field)
        if spec is None:
            raise FilterError(f"字段「{node.field}」不在筛选白名单内")
        if node.operator not in spec.operators:
            raise FilterError(
                f"字段「{node.field}」不支持操作符 {node.operator}"
            )
        if _is_empty_value(node.operator, node.value):
            return None

        # 虚拟字段（如 keyword）：走多字段 OR ilike / AND like_not 路径，不调 getattr
        if spec.is_virtual:
            # virtual_columns 为空时，自动用该表所有 text 类型字段作为搜索范围
            cols = spec.virtual_columns or [
                k for k, s in allowed_fields.items()
                if s.type == "text" and not s.is_virtual
            ]
            if not cols:
                raise FilterError(
                    f"虚拟字段「{node.field}」未声明 virtual_columns 且无 text 字段可搜索"
                )
            if node.operator not in ("like", "like_not"):
                raise FilterError(
                    f"虚拟字段「{node.field}」仅支持 like / like_not 操作符"
                )
            kw = f"%{node.value}%"
            # 虚拟列也可能有覆盖（如 keyword 中的 enterprise_name 需走外连接补名）
            def _vcol(c: str) -> ColumnElement:
                if column_overrides and c in column_overrides:
                    return column_overrides[c]()
                return getattr(model, c)
            if node.operator == "like":
                return or_(*[_vcol(c).ilike(kw) for c in cols])
            else:  # like_not
                return and_(*[~_vcol(c).ilike(kw) for c in cols])

        # 列覆盖优先（外键补名等场景），否则取模型自身列
        if column_overrides and node.field in column_overrides:
            col = column_overrides[node.field]()
        else:
            col = getattr(model, node.field)
        return _apply_operator(col, node.operator, node.value)

    if isinstance(node, FilterGroup):
        if not node.children:
            return None
        clauses: list[ColumnElement] = []
        for child in node.children:
            c = build_clause(
                model, child, allowed_fields, depth=depth + 1,
                column_overrides=column_overrides,
            )
            if c is not None:
                clauses.append(c)
        if not clauses:
            return None
        return and_(*clauses) if node.logic == "AND" else or_(*clauses)

    raise FilterError(f"未知 FilterNode 类型: {type(node).__name__}")


async def list_with_filter(
    model,
    repo: BaseRepository,
    filter_root: Union[FilterGroup, FilterLeaf, None],
    allowed_fields: dict[str, FieldSpec],
    *,
    page: int = 1,
    page_size: int = 20,
    order_by: Optional[Sequence] = None,
    joins: Optional[Sequence[JoinSpec]] = None,
    column_overrides: Optional[dict[str, ColumnOverride]] = None,
) -> tuple[list, int]:
    """通用复合条件分页查询入口。

    filter_root 为 None / 空 group / 全空值 leaf 时返回全表分页。
    - joins: 额外外连接 (target, onclause, isouter)，供列覆盖表达式引用
    - column_overrides: 字段 → 替代列表达式（如外连接 COALESCE 补名）
    其他参数同 BaseRepository.list。
    """
    stmt = select(model)
    if joins:
        for target, onclause, isouter in joins:
            stmt = stmt.join(target, onclause, isouter=isouter)
    if filter_root is not None:
        clause = build_clause(
            model, filter_root, allowed_fields, column_overrides=column_overrides
        )
        if clause is not None:
            stmt = stmt.where(clause)
    return await repo.list(
        stmt=stmt,
        page=page,
        page_size=page_size,
        order_by=order_by,
    )
