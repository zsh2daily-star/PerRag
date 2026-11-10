/**
 * 巡查记录表格配置 —— ReTablePage 基座的声明式列/筛选/批量/导入配置。
 *
 * 页面（index.vue）只保留本页特有逻辑：
 *   - 同批次同企业的行合并（spanMap + spanMethod）
 *   - 未关联企业（仅看未关联开关 / 行内关联 / 批次冲突提示）
 *   - 新建巡查单 / 派整改 / 完成整改 / 详情时间线
 *   - 导入结果弹窗自定义内容（import-result 插槽）
 */
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import { fmtDate } from "@/utils/date";
import type { BulkEditField } from "@/components/ReBulkEdit/index.vue";
import type {
  TableColumnConfig,
  TablePageConfig
} from "@/components/ReTablePage/types";
import {
  searchInspectionsWithFilter,
  updateInspection,
  importInspectionExcel
} from "@/api/inspections";

export type Level = "low" | "medium" | "high" | null;
export type Status = "open" | "rectifying" | "closed";

export interface Inspection {
  id: number;
  title: string;
  enterprise: string;
  enterprise_id: number;
  enterprise_code: string;
  inspection_batch: string;
  notice_number: string | null;
  hazard_index: number;
  hazard_count_total: number;
  inspect_date: string;
  level: Level;
  status: Status;
  deadline: string | null;
  description: string;
  category: string;
  location: string;
  region: string;
  industry: string;
  inspectors: string;
  inspector_user_id: number | null;
  responsible: string;
  rect_responsible_user_id: number | null;
  completed_at: string | null;
  review_date: string | null;
  remark: string;
  overdue: boolean;
}

// ── 字典与展示辅助 ──────────────────────────────────────
export const levelFilterOptions = [
  { value: "low", label: "低危" },
  { value: "medium", label: "中危" },
  { value: "high", label: "高危" }
];

export const statusFilterOptions = [
  { value: "open", label: "待处理" },
  { value: "rectifying", label: "整改中" },
  { value: "closed", label: "已闭环" },
  { value: "overdue", label: "超期" }
];

/** el-tag / el-timeline-item 的 type 联合 */
type TagType = "primary" | "success" | "warning" | "info" | "danger";

export const levelTagType = (level: Level): TagType => {
  if (!level) return "info";
  return { low: "success", medium: "warning", high: "danger" }[
    level
  ] as TagType;
};

export const levelText = (level: Level): string => {
  if (!level) return "未评定";
  return { low: "一般隐患", medium: "较大隐患", high: "重大隐患" }[level];
};

export const statusTagType: Record<Status, TagType> = {
  open: "primary",
  rectifying: "warning",
  closed: "success"
};

export const statusText: Record<Status, string> = {
  open: "待处理",
  rectifying: "整改中",
  closed: "已闭环"
};

// ── 列配置（key 与旧版列偏好一致，legacyColumnKeys 直接迁移）──
export const INSPECTION_COLUMNS: TableColumnConfig<Inspection>[] = [
  {
    key: "enterprise",
    prop: "enterprise",
    label: "企业",
    minWidth: 160,
    filterField: "enterprise_name",
    filterType: "text",
    filterPlaceholder: "搜索企业名称",
    slot: "cell-enterprise"
  },
  {
    key: "description",
    prop: "description",
    label: "隐患描述",
    minWidth: 240,
    filterField: "hazard_description",
    filterType: "text",
    filterPlaceholder: "搜索隐患内容",
    slot: "cell-description",
    exportValue: row =>
      (row.hazard_count_total > 1
        ? `(${row.hazard_index}/${row.hazard_count_total}) `
        : "") + (row.description ?? "")
  },
  {
    key: "batch",
    prop: "inspection_batch",
    label: "检查批次",
    width: 150,
    align: "center",
    filterField: "inspection_batch",
    filterType: "text",
    filterPlaceholder: "如 299-2026-09-01",
    editable: "inspection_batch"
  },
  {
    key: "notice_number",
    prop: "notice_number",
    label: "编号",
    width: 140,
    align: "center",
    filterField: "notice_number",
    filterType: "text",
    filterPlaceholder: "交办单编号",
    editable: "notice_number",
    exportValue: row => row.notice_number || ""
  },
  {
    key: "inspectors",
    prop: "inspectors",
    label: "检查人",
    width: 110,
    align: "center",
    filterField: "inspectors",
    filterType: "text",
    filterPlaceholder: "搜索检查人",
    editable: "inspectors"
  },
  {
    key: "responsible",
    prop: "responsible",
    label: "整改责任人",
    width: 110,
    align: "center",
    filterField: "rect_responsible_name",
    filterType: "text",
    filterPlaceholder: "搜索责任人",
    editable: "rect_responsible_name"
  },
  {
    key: "level",
    prop: "level",
    label: "隐患等级",
    width: 100,
    align: "center",
    filterField: "hazard_level",
    filterType: "enum",
    filterOptions: levelFilterOptions,
    slot: "cell-level",
    exportValue: row => levelText(row.level)
  },
  {
    key: "review_date",
    prop: "review_date",
    label: "复查时间",
    width: 120,
    align: "center",
    slot: "cell-review-date",
    exportHeader: "复查时间",
    exportValue: row => fmtDate(row.review_date)
  },
  {
    key: "status",
    prop: "status",
    label: "整改状态/完成时间",
    width: 180,
    align: "center",
    filterField: "status",
    filterType: "enum",
    filterOptions: statusFilterOptions,
    slot: "cell-status",
    exportValue: row =>
      row.completed_at || (row.overdue ? "超期" : statusText[row.status])
  }
];

/** 可批量修改字段（key 用后端字段名，直接传给 PUT /inspection-records/{id}） */
export const INSPECTION_BULK_FIELDS: BulkEditField[] = [
  { key: "hazard_description", label: "隐患描述", type: "text" },
  { key: "hazard_category", label: "隐患类别", type: "text" },
  { key: "inspection_batch", label: "检查批次", type: "text" },
  { key: "inspectors", label: "检查人（原文）", type: "text" },
  { key: "rect_responsible_name", label: "整改责任人（原文）", type: "text" },
  { key: "location", label: "隐患位置", type: "text" },
  { key: "inspection_date", label: "检查日期", type: "date" },
  { key: "rect_deadline", label: "整改期限", type: "date" },
  { key: "review_date", label: "复查时间", type: "date" },
  { key: "rect_completed_at", label: "整改完成时间", type: "date" },
  {
    key: "hazard_level",
    label: "隐患等级",
    type: "select",
    options: levelFilterOptions
  },
  {
    key: "status",
    label: "整改状态",
    type: "select",
    options: statusFilterOptions
  }
];

export interface InspectionConfigHooks {
  /** 超期行 class 与行合并所需（页面持有 spanMap） */
  spanMethod: TablePageConfig<Inspection>["spanMethod"];
  rowClassName: TablePageConfig<Inspection>["rowClassName"];
  onRowsLoaded: (rows: Inspection[]) => void;
  /** 导入成功后刷新未关联企业计数 */
  onImported: () => void;
  /** 重置时清理页面自有筛选状态（仅看未关联开关） */
  onReset: () => void;
}

export function buildInspectionConfig(
  hooks: InspectionConfigHooks
): TablePageConfig<Inspection> {
  return {
    storageKey: "inspection",
    title: "巡查记录",
    fetchApi: (filter, page, pageSize) =>
      // api 层签名是 Record<string, unknown>，直接 cast（FilterGroup 结构完全兼容）
      searchInspectionsWithFilter(
        filter as unknown as Record<string, unknown>,
        page,
        pageSize
      ),
    columns: INSPECTION_COLUMNS,
    legacyColumnKeys: { visible: "inspection-columns-visible" },
    keywordPlaceholder: "搜索企业 / 隐患描述 / 检查人 / 批次",
    bulkEditFields: INSPECTION_BULK_FIELDS,
    bulkUpdateApi: (row, payload) => updateInspection(row.id, payload as any),
    inlineUpdateApi: async (row, field, value) => {
      await updateInspection(row.id, { [field]: value } as any);
    },
    rowDisplayName: row => row.enterprise || `#${row.id}`,
    importApi: importInspectionExcel,
    importResultWidth: 680,
    exportFileName: "巡查记录",
    spanMethod: hooks.spanMethod,
    rowClassName: hooks.rowClassName,
    onRowsLoaded: hooks.onRowsLoaded,
    onImported: hooks.onImported,
    onReset: hooks.onReset
  };
}

export type { FilterGroup };
