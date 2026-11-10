/**
 * 危化品使用企业专项整治清单表格配置 —— ReTablePage 基座的声明式列/筛选/批量配置。
 *
 * 页面（index.vue）只保留本页特有逻辑：
 *   - 按企业名稳定重排序 + 企业名列纵向行合并（transformRows / spanMethod / onRowsLoaded）
 *   - 新建/编辑弹窗表单
 */
import type { BulkEditField } from "@/components/ReBulkEdit/index.vue";
import type {
  TableColumnConfig,
  TablePageConfig
} from "@/components/ReTablePage/types";
import {
  updateHazardousChemical,
  deleteHazardousChemical,
  searchHazardousChemicalsWithFilter
} from "@/api/hazardousChemicals";
import type { HazardousChemical } from "@/api/hazardousChemicals";

// ── 列配置（key 与列偏好 storage 对齐）──
export const HAZCHEM_COLUMNS: TableColumnConfig<HazardousChemical>[] = [
  // 企业信息
  {
    key: "enterprise_name",
    prop: "enterprise_name",
    label: "企业名称",
    minWidth: 200,
    filterField: "enterprise_name",
    filterType: "text",
    filterPlaceholder: "搜索企业名称",
    slot: "cell-enterprise_name"
  },
  {
    key: "enterprise_region",
    prop: "enterprise_region",
    label: "县（市、区）",
    minWidth: 140
  },
  {
    key: "town",
    prop: "town",
    label: "镇（区、街道）",
    minWidth: 140,
    filterField: "town",
    filterType: "text",
    filterPlaceholder: "搜索镇街"
  },
  {
    key: "enterprise_industry",
    prop: "enterprise_industry",
    label: "行业类型",
    minWidth: 100
  },
  // 危化品明细
  {
    key: "hazchem_name",
    prop: "hazchem_name",
    label: "危化品名称",
    minWidth: 200,
    filterField: "hazchem_name",
    filterType: "text",
    filterPlaceholder: "搜索危化品名称",
    editable: "hazchem_name"
  },
  {
    key: "hazchem_state",
    prop: "hazchem_state",
    label: "危化品状态",
    width: 100,
    align: "center",
    filterField: "hazchem_state",
    filterType: "text",
    filterPlaceholder: "搜索状态"
  },
  {
    key: "reserve",
    prop: "reserve",
    label: "储量",
    minWidth: 100,
    align: "right",
    filterField: "reserve",
    filterType: "text",
    filterPlaceholder: "搜索储量",
    editable: "reserve",
    formatter: r => r.reserve || "—"
  },
  {
    key: "max_reserve",
    prop: "max_reserve",
    label: "设计最大储量",
    minWidth: 120,
    align: "right",
    filterField: "max_reserve",
    filterType: "text",
    filterPlaceholder: "搜索设计最大储量",
    editable: "max_reserve",
    formatter: r => r.max_reserve || "—"
  },
  {
    key: "annual_usage",
    prop: "annual_usage",
    label: "年度使用量",
    minWidth: 110,
    align: "right",
    filterField: "annual_usage",
    filterType: "text",
    filterPlaceholder: "搜索年度使用量",
    editable: "annual_usage",
    formatter: r => r.annual_usage || "—"
  },
  {
    key: "usage",
    prop: "usage",
    label: "危化品用途",
    minWidth: 100,
    filterField: "usage",
    filterType: "text",
    filterPlaceholder: "搜索用途",
    editable: "usage"
  },
  {
    key: "storage_method",
    prop: "storage_method",
    label: "存储方式",
    minWidth: 100,
    filterField: "storage_method",
    filterType: "text",
    filterPlaceholder: "搜索存储方式",
    editable: "storage_method"
  },
  {
    key: "storage_location",
    prop: "storage_location",
    label: "存储位置",
    minWidth: 160,
    filterField: "storage_location",
    filterType: "text",
    filterPlaceholder: "搜索存储位置",
    editable: "storage_location"
  },
  {
    key: "major_hazard",
    prop: "major_hazard",
    label: "是否构成重大危险源",
    minWidth: 160,
    align: "center",
    filterField: "major_hazard",
    filterType: "text",
    filterPlaceholder: "搜索重大危险源",
    slot: "cell-major_hazard",
    exportValue: r => r.major_hazard || ""
  },
  {
    key: "remark",
    prop: "remark",
    label: "备注",
    minWidth: 120,
    filterField: "remark",
    filterType: "text",
    filterPlaceholder: "搜索备注",
    editable: "remark"
  }
];

/** 列显示面板分组 */
export const HAZCHEM_COLUMN_GROUPS = [
  {
    name: "企业信息",
    keys: ["enterprise_name", "enterprise_region", "town", "enterprise_industry"]
  },
  {
    name: "危化品明细",
    keys: [
      "hazchem_name",
      "hazchem_state",
      "reserve",
      "max_reserve",
      "annual_usage",
      "usage",
      "storage_method",
      "storage_location",
      "major_hazard",
      "remark"
    ]
  }
];

/** 可批量修改字段 */
export const HAZCHEM_BULK_FIELDS: BulkEditField[] = [
  { key: "town", label: "镇（区、街道）", type: "text" },
  { key: "hazchem_state", label: "危化品状态", type: "text" },
  { key: "usage", label: "危化品用途", type: "text" },
  { key: "storage_method", label: "存储方式", type: "text" },
  { key: "storage_location", label: "存储位置", type: "text" },
  { key: "major_hazard", label: "是否构成重大危险源", type: "text" },
  { key: "remark", label: "备注", type: "text" }
];

export interface HazchemConfigHooks {
  /** 企业名列纵向合并（页面持有 mergeInfo） */
  spanMethod: TablePageConfig<HazardousChemical>["spanMethod"];
  /** 按企业名稳定重排序（行合并依赖同组行连续） */
  transformRows: (rows: HazardousChemical[]) => HazardousChemical[];
  /** 加载完成后重建合并信息 */
  onRowsLoaded: (rows: HazardousChemical[]) => void;
}

export function buildHazchemConfig(
  hooks: HazchemConfigHooks
): TablePageConfig<HazardousChemical> {
  return {
    storageKey: "hazchem",
    title: "危化品使用企业专项整治清单",
    fetchApi: (filter, page, pageSize) =>
      searchHazardousChemicalsWithFilter(filter, page, pageSize),
    columns: HAZCHEM_COLUMNS,
    columnGroups: HAZCHEM_COLUMN_GROUPS,
    legacyColumnKeys: {
      visible: "hazchem-columns-visible-v1",
      order: "hazchem-columns-order-v1"
    },
    keywordPlaceholder:
      "搜索企业名 / 危化品名 / 存储位置 / 用途 / 备注",
    bulkEditFields: HAZCHEM_BULK_FIELDS,
    bulkUpdateApi: (row, payload) =>
      updateHazardousChemical(row.id, payload as any),
    inlineUpdateApi: async (row, field, value) => {
      await updateHazardousChemical(row.id, { [field]: value } as any);
    },
    deleteApi: row => deleteHazardousChemical(row.id),
    deleteConfirmText: "确定删除该危化品记录吗？",
    rowDisplayName: row =>
      row.hazchem_name || row.enterprise_name || `#${row.id}`,
    exportFileName: "危化品使用企业",
    exportSheetName: "危化品使用企业",
    spanMethod: hooks.spanMethod,
    transformRows: hooks.transformRows,
    onRowsLoaded: hooks.onRowsLoaded
  };
}
