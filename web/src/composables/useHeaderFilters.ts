/**
 * 表头筛选 composable —— 企业列表 / 巡查台账共用的同一套底层逻辑。
 *
 * 四类列筛选状态（text / number / enum / bool）统一同步到 ReFilterBuilder
 * 的 FilterGroup 条件树，由 POST /{table}/query 经后端 filter_service 解析。
 * 本文件不耦合任何具体业务字段，调用方只提供 filter 根组与查询回调。
 */
import { reactive, type Ref } from "vue";
import type { FilterGroup, FilterLeaf, FilterNode } from "@/components/ReFilterBuilder/types";

/** enum 筛选中代表"(空)"的特殊值（不会与真实枚举冲突） */
export const NULL_FILTER_VALUE = "__null__";
/** enum 筛选中代表"(非空)"的特殊值（不会与真实枚举冲突） */
export const NOT_NULL_FILTER_VALUE = "__not_null__";

/** 判断节点是否属于指定字段的筛选条件（leaf 或包含该字段 leaf 的 OR 子组） */
const isFieldNode = (n: FilterNode, field: string): boolean => {
  if (n.type === "leaf") return n.field === field;
  return n.type === "group" && n.children.some(c => c.type === "leaf" && c.field === field);
};

/** 从条件组中移除指定字段的全部旧条件 */
const removeFieldConditions = (group: FilterGroup, field: string) => {
  for (let i = group.children.length - 1; i >= 0; i--) {
    if (isFieldNode(group.children[i], field)) group.children.splice(i, 1);
  }
};

export interface HeaderFilterHandlers {
  /** 某字段当前是否有激活的 text 筛选 */
  hasTextFilter: (field: string) => boolean;
  /** 某字段当前是否有激活的 number 筛选 */
  hasNumberFilter: (field: string) => boolean;
  /** 某字段当前是否有激活的 date 筛选 */
  hasDateFilter: (field: string) => boolean;
  /** 某字段当前是否有激活的 enum 筛选 */
  hasEnumFilter: (field: string) => boolean;
  /** 某字段当前是否有激活的 bool 筛选 */
  hasBoolFilter: (field: string) => boolean;
  /** text 列输入框当前值 */
  textValue: (field: string) => string;
  setTextValue: (field: string, v: string) => void;
  /** number 列范围当前值 */
  numberRange: (field: string) => { min: string; max: string };
  /** date 列范围当前值（与 number 同构，值为 YYYY-MM-DD） */
  dateRange: (field: string) => { min: string; max: string };
  /** enum 列勾选项当前值 */
  enumValue: (field: string) => string[];
  setEnumValue: (field: string, v: string[]) => void;
  /** bool 列当前值 */
  boolValue: (field: string) => "true" | "false" | "";
  setBoolValue: (field: string, v: "true" | "false" | "") => void;
  /** text/number/date 列的空值模式：null=为空，not_null=不为空，""=未启用 */
  nullMode: (field: string) => "" | "null" | "not_null";
  /** 快捷应用「为空 / 不为空」——与该列的值筛选互斥，应用后替换该字段全部条件 */
  applyNullness: (field: string, mode: "null" | "not_null") => void;

  applyTextFilter: (field: string) => void;
  clearTextFilter: (field: string) => void;
  applyNumberFilter: (field: string) => void;
  clearNumberFilter: (field: string) => void;
  applyDateFilter: (field: string) => void;
  clearDateFilter: (field: string) => void;
  applyEnumFilter: (field: string) => void;
  clearEnumFilter: (field: string) => void;
  applyBoolFilter: (field: string) => void;
  clearBoolFilter: (field: string) => void;
  /** 清空所有列筛选面板的本地状态（不动 filter 树，由调用方重建） */
  reset: () => void;
}

/**
 * @param filter          筛选条件根组（与 ReFilterBuilder 共享同一 ref）
 * @param onApply         条件变更回调（通常是 page=1 + fetchList）
 * @param arrayEnumFields 数组枚举列字段名集合：多选勾选用 overlaps（&& 含任一）
 *                        而非标量列的 in；如 enterprise_category(text[] 多标签)
 */
export function useHeaderFilters(
  filter: Ref<FilterGroup>,
  onApply: () => void,
  arrayEnumFields: readonly string[] = []
): HeaderFilterHandlers {
  const arrayEnumSet = new Set(arrayEnumFields);
  const textFilterValues = reactive<Record<string, string>>({});
  const numberFilterRanges = reactive<Record<string, { min: string; max: string }>>({});
  const dateFilterRanges = reactive<Record<string, { min: string; max: string }>>({});
  const enumFilterValues = reactive<Record<string, string[]>>({});
  const boolFilterValues = reactive<Record<string, "true" | "false" | "">>({});
  /** text/number/date 列的空值模式（与值筛选互斥） */
  const nullModes = reactive<Record<string, "" | "null" | "not_null">>({});

  const ensureRange = (
    store: Record<string, { min: string; max: string }>,
    field: string
  ) => {
    if (!store[field]) store[field] = { min: "", max: "" };
    return store[field];
  };

  // ── text ──
  const applyTextFilter = (field: string) => {
    const group = filter.value;
    removeFieldConditions(group, field);
    nullModes[field] = "";
    const val = textFilterValues[field]?.trim();
    if (val) {
      group.children.push({ type: "leaf", field, operator: "like", value: val });
    }
    onApply();
  };
  const clearTextFilter = (field: string) => {
    textFilterValues[field] = "";
    nullModes[field] = "";
    removeFieldConditions(filter.value, field);
    onApply();
  };

  // ── number ──
  const applyNumberFilter = (field: string) => {
    const group = filter.value;
    removeFieldConditions(group, field);
    nullModes[field] = "";
    const range = ensureRange(numberFilterRanges, field);
    if (range.min) group.children.push({ type: "leaf", field, operator: "gte", value: Number(range.min) });
    if (range.max) group.children.push({ type: "leaf", field, operator: "lte", value: Number(range.max) });
    onApply();
  };
  const clearNumberFilter = (field: string) => {
    numberFilterRanges[field] = { min: "", max: "" };
    nullModes[field] = "";
    removeFieldConditions(filter.value, field);
    onApply();
  };

  // ── date（范围 → gte/lte，值为 YYYY-MM-DD 字符串，后端转 date）──
  const applyDateFilter = (field: string) => {
    const group = filter.value;
    removeFieldConditions(group, field);
    nullModes[field] = "";
    const range = ensureRange(dateFilterRanges, field);
    if (range.min) group.children.push({ type: "leaf", field, operator: "gte", value: range.min });
    if (range.max) group.children.push({ type: "leaf", field, operator: "lte", value: range.max });
    onApply();
  };
  const clearDateFilter = (field: string) => {
    dateFilterRanges[field] = { min: "", max: "" };
    nullModes[field] = "";
    removeFieldConditions(filter.value, field);
    onApply();
  };

  /**
   * 快捷「为空 / 不为空」（text/number/date 列）：
   * 替换该字段全部条件为单个 is_null / is_not_null leaf，并清空值筛选本地态。
   */
  const applyNullness = (field: string, mode: "null" | "not_null") => {
    const group = filter.value;
    removeFieldConditions(group, field);
    nullModes[field] = mode;
    textFilterValues[field] = "";
    numberFilterRanges[field] = { min: "", max: "" };
    dateFilterRanges[field] = { min: "", max: "" };
    group.children.push({
      type: "leaf",
      field,
      operator: mode === "null" ? "is_null" : "is_not_null",
      value: null
    });
    onApply();
  };

  // ── enum（含"(空)"/"(非空)"哨兵 → is_null / is_not_null 组合）──
  const applyEnumFilter = (field: string) => {
    const group = filter.value;
    removeFieldConditions(group, field);
    const selected = enumFilterValues[field] ?? [];
    if (!selected.length) { onApply(); return; }

    // 数组枚举列（text[] 多标签）：勾选多个值 = 含任一标签（overlaps &&）；
    // 标量枚举列：勾选多个值 = 等于其一（in）
    const multiOp = arrayEnumSet.has(field) ? "overlaps" : "in";
    const hasNull = selected.includes(NULL_FILTER_VALUE);
    const hasNotNull = selected.includes(NOT_NULL_FILTER_VALUE);
    const normalValues = selected.filter(
      v => v !== NULL_FILTER_VALUE && v !== NOT_NULL_FILTER_VALUE
    );

    if (normalValues.length) {
      const baseLeaf: FilterLeaf = {
        type: "leaf", field, operator: multiOp, value: normalValues
      };
      // 只勾「空」+ 具体值 → 值匹配 OR 为空；
      // in/overlaps 本身已隐含非空，勾「非空」（或两个都勾）时只保留值匹配
      if (hasNull && !hasNotNull) {
        group.children.push({
          type: "group", logic: "OR",
          children: [
            baseLeaf,
            { type: "leaf", field, operator: "is_null", value: null }
          ]
        });
      } else {
        group.children.push(baseLeaf);
      }
    } else if (hasNull && !hasNotNull) {
      group.children.push({ type: "leaf", field, operator: "is_null", value: null });
    } else if (hasNotNull && !hasNull) {
      group.children.push({ type: "leaf", field, operator: "is_not_null", value: null });
    }
    // 两个哨兵都勾或都不勾 = 不加条件（全部）
    onApply();
  };
  const clearEnumFilter = (field: string) => {
    enumFilterValues[field] = [];
    removeFieldConditions(filter.value, field);
    onApply();
  };

  // ── bool（单字段）──
  const applyBoolFilter = (field: string) => {
    const group = filter.value;
    removeFieldConditions(group, field);
    const val = boolFilterValues[field];
    if (val === "true") {
      group.children.push({ type: "leaf", field, operator: "is_true", value: null });
    } else if (val === "false") {
      group.children.push({ type: "leaf", field, operator: "is_false", value: null });
    }
    onApply();
  };
  const clearBoolFilter = (field: string) => {
    boolFilterValues[field] = "";
    removeFieldConditions(filter.value, field);
    onApply();
  };

  return {
    hasTextFilter: field => !!nullModes[field] || !!textFilterValues[field]?.trim(),
    hasNumberFilter: field => {
      if (nullModes[field]) return true;
      const r = numberFilterRanges[field];
      return !!r && (r.min !== "" || r.max !== "");
    },
    hasDateFilter: field => {
      if (nullModes[field]) return true;
      const r = dateFilterRanges[field];
      return !!r && (r.min !== "" || r.max !== "");
    },
    hasEnumFilter: field => (enumFilterValues[field]?.length ?? 0) > 0,
    hasBoolFilter: field => !!boolFilterValues[field],
    textValue: field => textFilterValues[field] ?? "",
    setTextValue: (field, v) => { textFilterValues[field] = v; },
    numberRange: field => ensureRange(numberFilterRanges, field),
    dateRange: field => ensureRange(dateFilterRanges, field),
    enumValue: field => enumFilterValues[field] ?? [],
    setEnumValue: (field, v) => { enumFilterValues[field] = v; },
    boolValue: field => boolFilterValues[field] ?? "",
    setBoolValue: (field, v) => { boolFilterValues[field] = v; },
    nullMode: field => nullModes[field] ?? "",
    applyNullness,
    applyTextFilter,
    clearTextFilter,
    applyNumberFilter,
    clearNumberFilter,
    applyDateFilter,
    clearDateFilter,
    applyEnumFilter,
    clearEnumFilter,
    applyBoolFilter,
    clearBoolFilter,
    reset: () => {
      for (const k of Object.keys(textFilterValues)) textFilterValues[k] = "";
      for (const k of Object.keys(numberFilterRanges)) {
        numberFilterRanges[k] = { min: "", max: "" };
      }
      for (const k of Object.keys(dateFilterRanges)) {
        dateFilterRanges[k] = { min: "", max: "" };
      }
      for (const k of Object.keys(enumFilterValues)) enumFilterValues[k] = [];
      for (const k of Object.keys(boolFilterValues)) boolFilterValues[k] = "";
      for (const k of Object.keys(nullModes)) nullModes[k] = "";
    }
  };
}
