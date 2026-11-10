/**
 * 厂中厂专项整治清单表格配置 —— ReTablePage 基座的声明式列/筛选/批量/导入导出配置。
 *
 * 页面（index.vue）只保留本页特有逻辑：
 *   - 按出租方稳定重排序 + 出租方列纵向行合并（transformRows / spanMethod / onRowsLoaded）
 *   - 新建/编辑弹窗表单
 *   - 导入结果弹窗自定义内容（import-result 插槽）
 */
import type { BulkEditField } from "@/components/ReBulkEdit/index.vue";
import type {
  TableColumnConfig,
  TablePageConfig
} from "@/components/ReTablePage/types";
import {
  updateFactoryInFactory,
  deleteFactoryInFactory,
  importFactoryInFactoryExcel,
  searchFactoryInFactoryWithFilter
} from "@/api/factoryInFactory";
import type { FactoryInFactory } from "@/api/factoryInFactory";

/** 联系方式组合列导出：联系人 + 电话（与单元格两行显示同信息） */
const joinContact = (person: string | null, phone: string | null) =>
  [person, phone].filter(Boolean).join(" ");

// ── 列配置（key 与旧版列偏好 v3 一致，legacyColumnKeys 直接迁移）──
export const FACTORY_COLUMNS: TableColumnConfig<FactoryInFactory>[] = [
  // 批次信息
  {
    key: "address",
    prop: "address",
    label: "具体地址",
    minWidth: 180,
    filterField: "address",
    filterType: "text",
    filterPlaceholder: "搜索地址"
  },
  {
    key: "site_category",
    prop: "site_category",
    label: "场所类别",
    minWidth: 120,
    filterField: "site_category",
    filterType: "text",
    filterPlaceholder: "搜索类别"
  },
  // 出租方
  {
    key: "lessor_name",
    prop: "lessor_name",
    label: "出租方",
    minWidth: 180,
    filterField: "lessor_name",
    filterType: "text",
    filterPlaceholder: "搜索出租方",
    slot: "cell-lessor_name"
  },
  {
    key: "lessor_contact",
    prop: "lessor_contact",
    label: "出租方联系方式",
    minWidth: 140,
    filterField: "lessor_contact_person",
    filterType: "text",
    filterPlaceholder: "搜索联系人",
    slot: "cell-lessor_contact",
    exportValue: r =>
      joinContact(r.lessor_contact_person, r.lessor_contact_phone)
  },
  {
    key: "lessor_use_area",
    prop: "lessor_use_area",
    label: "出租方面积",
    minWidth: 110,
    filterField: "lessor_use_area",
    filterType: "text",
    filterPlaceholder: "搜索面积",
    editable: "lessor_use_area",
    formatter: r => r.lessor_use_area || "—"
  },
  // 承租方
  {
    key: "tenant_name",
    prop: "tenant_name",
    label: "承租方",
    minWidth: 180,
    filterField: "tenant_name",
    filterType: "text",
    filterPlaceholder: "搜索承租方",
    slot: "cell-tenant_name"
  },
  {
    key: "tenant_contact",
    label: "承租方联系方式",
    minWidth: 140,
    filterField: "tenant_contact_person",
    filterType: "text",
    filterPlaceholder: "搜索联系人",
    slot: "cell-tenant_contact",
    exportValue: r =>
      joinContact(r.tenant_contact_person, r.tenant_contact_phone)
  },
  {
    key: "tenant_area",
    prop: "tenant_area",
    label: "承租面积",
    width: 100,
    align: "right",
    filterField: "tenant_area",
    filterType: "number",
    formatter: r => (r.tenant_area != null ? r.tenant_area : "—")
  },
  {
    key: "agreement_signed",
    prop: "agreement_signed",
    label: "是否签订协议",
    width: 110,
    align: "center",
    filterField: "agreement_signed",
    filterType: "bool",
    slot: "cell-agreement_signed",
    exportValue: r =>
      r.agreement_signed == null ? "" : r.agreement_signed ? "是" : "否"
  }
];

/** 列显示面板分组 */
export const FACTORY_COLUMN_GROUPS = [
  { name: "批次信息", keys: ["address", "site_category"] },
  {
    name: "出租方",
    keys: ["lessor_name", "lessor_contact", "lessor_use_area"]
  },
  {
    name: "承租方",
    keys: ["tenant_name", "tenant_contact", "tenant_area", "agreement_signed"]
  }
];

/** 可批量修改字段：排除出租方/承租方名称及联系方式组合列 */
export const FACTORY_BULK_FIELDS: BulkEditField[] = [
  { key: "address", label: "具体地址", type: "text" },
  { key: "site_category", label: "场所类别", type: "text" },
  { key: "lessor_use_area", label: "出租方面积", type: "text" },
  { key: "tenant_area", label: "承租面积", type: "number" },
  { key: "agreement_signed", label: "是否签订协议", type: "bool" }
];

export interface FactoryConfigHooks {
  /** 出租方列纵向合并（页面持有 mergeInfo） */
  spanMethod: TablePageConfig<FactoryInFactory>["spanMethod"];
  /** 按出租方稳定重排序（行合并依赖同组行连续） */
  transformRows: (rows: FactoryInFactory[]) => FactoryInFactory[];
  /** 加载完成后重建合并信息 */
  onRowsLoaded: (rows: FactoryInFactory[]) => void;
}

export function buildFactoryConfig(
  hooks: FactoryConfigHooks
): TablePageConfig<FactoryInFactory> {
  return {
    storageKey: "factory",
    title: "厂中厂专项整治清单",
    fetchApi: (filter, page, pageSize) =>
      searchFactoryInFactoryWithFilter(filter, page, pageSize),
    columns: FACTORY_COLUMNS,
    columnGroups: FACTORY_COLUMN_GROUPS,
    legacyColumnKeys: {
      visible: "factory-columns-visible-v3",
      order: "factory-columns-order-v3"
    },
    keywordPlaceholder: "搜索出租方 / 承租方 / 批次 / 隐患 / 排查人 / 督改人",
    bulkEditFields: FACTORY_BULK_FIELDS,
    bulkUpdateApi: (row, payload) =>
      updateFactoryInFactory(row.id, payload as any),
    inlineUpdateApi: async (row, field, value) => {
      await updateFactoryInFactory(row.id, { [field]: value } as any);
    },
    deleteApi: row => deleteFactoryInFactory(row.id),
    deleteConfirmText: "确定删除该记录吗？（承租方：{name}）",
    rowDisplayName: row => row.tenant_name || `#${row.id}`,
    importApi: importFactoryInFactoryExcel,
    importResultWidth: 640,
    exportFileName: "厂中厂整治",
    exportSheetName: "厂中厂整治",
    spanMethod: hooks.spanMethod,
    transformRows: hooks.transformRows,
    onRowsLoaded: hooks.onRowsLoaded
  };
}
