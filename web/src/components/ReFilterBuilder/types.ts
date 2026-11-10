/**
 * 动态筛选条件构建器类型定义
 *
 * 数据结构与后端 app/services/filter_service.py 的 Pydantic 模型一致：
 *   - FilterGroup: { type:'group', logic:'AND'|'OR', children: FilterNode[] }
 *   - FilterLeaf:  { type:'leaf', field, operator, value }
 *   - FilterNode = FilterGroup | FilterLeaf（discriminated union，靠 type 字段路由）
 *
 * 字段元数据 FilterFieldSpec 来自 GET /{table}/meta/filter-fields，
 * 驱动 UI 渲染：field.type 决定值控件，field.operators 决定可选操作符。
 */

export type Operator =
  | "eq"
  | "neq"
  | "like"
  | "like_not"
  | "in"
  | "not_in"
  | "gt"
  | "lt"
  | "gte"
  | "lte"
  | "is_true"
  | "is_false"
  | "is_null"
  | "is_not_null"
  | "contains"
  | "overlaps";

export type FieldType =
  | "enum"
  | "enum_array"
  | "text"
  | "bool"
  | "number"
  | "date"
  | "keyword";

/** 字段元数据（前端 UI 渲染 + 后端白名单同源） */
export interface FilterFieldSpec {
  /** 字段 key（= 后端 SQLAlchemy 列名 / 虚拟字段名） */
  key: string;
  /** 界面显示名 */
  label: string;
  /** 字段类型，决定值控件与 operator 集合 */
  type: FieldType;
  /** 允许的操作符列表 */
  operators: Operator[];
  /** 固定枚举值（内联） */
  options?: string[] | null;
  /** 动态枚举值来源 endpoint（如 "/enterprises/meta/industries"） */
  options_source?: string | null;
  /** 是否虚拟字段（如 keyword 走多字段 OR） */
  is_virtual?: boolean;
  /** 虚拟字段实际 OR 的列名（仅 is_virtual=true 时填） */
  virtual_columns?: string[];
}

export interface FilterLeaf {
  type: "leaf";
  field: string;
  operator: Operator;
  value: unknown;
}

export interface FilterGroup {
  type: "group";
  logic: "AND" | "OR";
  children: FilterNode[];
}

export type FilterNode = FilterLeaf | FilterGroup;

/** 操作符显示名（中文） */
export const OPERATOR_LABELS: Record<Operator, string> = {
  eq: "等于",
  neq: "不等于",
  like: "包含",
  like_not: "不包含",
  in: "属于（任一）",
  not_in: "不属于",
  gt: "大于",
  lt: "小于",
  gte: "大于等于",
  lte: "小于等于",
  is_true: "是",
  is_false: "否",
  is_null: "为空",
  is_not_null: "不为空",
  contains: "包含全部标签",
  overlaps: "含任一标签"
};

/** 各字段类型默认 operator（首次添加 leaf 时用） */
export const DEFAULT_OPERATOR_BY_TYPE: Record<FieldType, Operator> = {
  enum: "eq",
  enum_array: "contains",
  text: "like",
  bool: "is_true",
  number: "eq",
  date: "eq",
  keyword: "like"
};

/** 创建空 leaf */
export const makeLeaf = (field: string, type: FieldType): FilterLeaf => ({
  type: "leaf",
  field,
  operator: DEFAULT_OPERATOR_BY_TYPE[type] ?? "eq",
  value:
    type === "bool"
      ? null
      : type === "enum" || type === "enum_array"
        ? []
        : ""
});

/** 创建空 group（可带初始 children） */
export const makeGroup = (
  logic: "AND" | "OR" = "AND",
  children: FilterNode[] = []
): FilterGroup => ({
  type: "group",
  logic,
  children
});

/** 创建"关键词"默认条件（enterprises 首页占位用） */
export const makeDefaultKeywordLeaf = (): FilterLeaf => ({
  type: "leaf",
  field: "keyword",
  operator: "like",
  value: ""
});
