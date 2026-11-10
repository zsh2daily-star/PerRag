/**
 * 企业表格配置 —— 企业主表 / 有限空间专页 / 危化品专页共用同一份列定义。
 *
 * 列只在这里声明一次（显示/筛选/导出/批量修改全部派生自它），
 * 页面通过 buildEnterpriseConfig 传入列子集 + 预设筛选即成专页。
 */
import type { Ref } from "vue";
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import type {
  TablePageConfig,
  TableColumnConfig
} from "@/components/ReTablePage/types";
import type { BulkEditField } from "@/components/ReBulkEdit/index.vue";
import {
  searchEnterprisesWithFilter,
  updateEnterprise,
  deleteEnterprise
} from "@/api/enterprises";
import type {
  Enterprise,
  EnterpriseCategory,
  FireLevel,
  AssetAttribute,
  ConfinedSpace
} from "@/api/enterprises";

// ── 字典 ──────────────────────────────────────────────────
/** 行业固定字典 */
export const industryOptions = ["纺织", "机械", "建材", "轻工", "化工", "其他"];
/** 企业类型字典（多标签） */
export const categoryOptions: EnterpriseCategory[] = [
  "劳密",
  "厂中厂出租",
  "厂中厂承租",
  "小场所",
  "规上",
  "搬离",
  "其他"
];
/** 资产属性字典 */
export const assetOptions: AssetAttribute[] = ["韩资", "台资", "其他"];
/** 消防重点单位等级字典 */
export const fireLevelOptions: FireLevel[] = ["二级", "三级", "重点单位"];
/** 有限空间位置字典 */
export const confinedSpaceOptions: ConfinedSpace[] = ["地上", "地下"];

/** 安全风险点布尔字段（界面标签不带"是否"） */
export type SafetyFlagKey =
  | "has_natural_gas"
  | "is_injection_molding"
  | "has_dormitory"
  | "has_hazardous_chemicals"
  | "has_hazardous_waste"
  | "involves_dust_explosion"
  | "is_new_energy_welding"
  | "has_large_storage_tank";

export const safetyFlags: { key: SafetyFlagKey; label: string }[] = [
  { key: "has_natural_gas", label: "天然气" },
  { key: "is_injection_molding", label: "注塑企业" },
  { key: "has_dormitory", label: "宿舍" },
  { key: "has_hazardous_chemicals", label: "危化品" },
  { key: "has_hazardous_waste", label: "危废品" },
  { key: "involves_dust_explosion", label: "粉尘爆炸" },
  { key: "is_new_energy_welding", label: "新能源电焊" },
  { key: "has_large_storage_tank", label: "大型储罐" }
];

// ── 单元格辅助 ────────────────────────────────────────────
/** 判断企业是否有任一安全风险标记（含有限空间） */
export const hasAnyFlag = (row: Enterprise) =>
  safetyFlags.some(f => !!row[f.key]) ||
  (row.confined_space || []).length > 0;

/** extra 多值（数组）用顿号拼接，单值原样返回 */
export const formatExtraValue = (v: string | string[]): string =>
  Array.isArray(v) ? v.filter(Boolean).join("、") : (v ?? "");

/** 提取有内容的自定义字段 [字段名, 展示值]，空值字段不展示 */
export const extraEntries = (row: Enterprise): [string, string][] =>
  Object.entries(row.extra || {})
    .map(([k, v]) => [k.trim(), formatExtraValue(v as string | string[])])
    .filter(([k, v]) => k && v) as [string, string][];

/** 企业类型标签配色 */
export const categoryTagType = (cat: string) =>
  cat === "劳密" || cat === "规上"
    ? "danger"
    : cat === "厂中厂出租" || cat === "厂中厂承租"
      ? "warning"
      : cat === "小场所" || cat === "搬离"
        ? "info"
        : "primary";

const dash = (v: any) => (v === null || v === undefined || v === "" ? "—" : v);

// ── 列定义（唯一事实来源）─────────────────────────────────
/** 各列的筛选/显示/导出全部在此声明；样式化单元格经 slot 注入（EnterpriseTable.vue 提供） */
export const ENTERPRISE_COLUMNS: TableColumnConfig<Enterprise>[] = [
  // 基础信息
  {
    key: "name",
    prop: "name",
    label: "名称",
    minWidth: 220,
    alwaysVisible: true,
    filterType: "text",
    filterPlaceholder: "搜索企业名称",
    editable: "name",
    exportHeader: "企业名称"
  },
  {
    key: "code",
    prop: "code",
    label: "信用代码",
    width: 190,
    filterType: "text",
    filterPlaceholder: "搜索信用代码",
    editable: "code",
    exportHeader: "信用代码",
    exportValue: r => r.code || ""
  },
  {
    key: "category",
    label: "类型",
    minWidth: 150,
    slot: "cell-category",
    filterField: "enterprise_category",
    filterType: "enum",
    filterOptions: categoryOptions,
    filterArrayEnum: true,
    exportHeader: "企业类型",
    exportValue: r => (r.enterprise_category || []).join("、")
  },
  {
    key: "asset",
    label: "资产属性",
    width: 110,
    align: "center",
    slot: "cell-asset",
    filterField: "asset_attribute",
    filterType: "enum",
    filterOptions: assetOptions,
    exportHeader: "资产属性",
    exportValue: r => r.asset_attribute || ""
  },
  {
    key: "industry",
    label: "行业",
    width: 100,
    align: "center",
    slot: "cell-industry",
    filterField: "industry",
    filterType: "enum",
    filterOptions: industryOptions,
    exportHeader: "行业",
    exportValue: r => r.industry || ""
  },
  {
    key: "region",
    label: "区域",
    width: 110,
    align: "center",
    slot: "cell-region",
    filterField: "region",
    filterType: "enum",
    exportHeader: "区域",
    exportValue: r => r.region || ""
  },
  {
    key: "legal_person",
    label: "法人",
    width: 115,
    filterField: "legal_person",
    filterType: "text",
    filterPlaceholder: "搜索法人",
    editable: "legal_person",
    formatter: r => dash(r.legal_person),
    exportHeader: "法人",
    exportValue: r => r.legal_person || ""
  },
  {
    key: "person_in_charge",
    label: "负责人",
    width: 170,
    filterField: "person_in_charge",
    filterType: "text",
    filterPlaceholder: "搜索负责人",
    editable: "person_in_charge",
    formatter: r => dash(r.person_in_charge),
    exportHeader: "企业负责人",
    exportValue: r => r.person_in_charge || ""
  },
  {
    key: "safety_officer",
    label: "安全员",
    width: 115,
    filterField: "safety_officer",
    filterType: "text",
    filterPlaceholder: "搜索安全员",
    editable: "safety_officer",
    formatter: r => dash(r.safety_officer),
    exportHeader: "企业安全员",
    exportValue: r => r.safety_officer || ""
  },
  {
    key: "address",
    prop: "address",
    label: "地址",
    minWidth: 220,
    filterField: "address",
    filterType: "text",
    filterPlaceholder: "搜索地址",
    editable: "address",
    exportHeader: "地址",
    exportValue: r => r.address || ""
  },
  // 规模信息
  {
    key: "employee_count",
    label: "人数",
    width: 90,
    align: "center",
    slot: "cell-employee_count",
    filterField: "employee_count",
    filterType: "number",
    exportHeader: "职工人数",
    exportValue: r => (r.employee_count != null ? r.employee_count : "")
  },
  {
    key: "annual_output_value",
    label: "年产值",
    width: 110,
    align: "center",
    filterField: "annual_output_value",
    filterType: "number",
    formatter: r =>
      r.annual_output_value != null ? r.annual_output_value : "—",
    exportHeader: "年产值(万元)",
    exportValue: r =>
      r.annual_output_value != null ? r.annual_output_value : ""
  },
  // 安全信息
  {
    key: "fire_level",
    label: "消防",
    width: 90,
    align: "center",
    slot: "cell-fire_level",
    filterField: "fire_level",
    filterType: "enum",
    filterOptions: fireLevelOptions,
    exportHeader: "消防等级",
    exportValue: r => r.fire_level || ""
  },
  {
    key: "flags",
    label: "风险标记",
    minWidth: 240,
    slot: "cell-flags",
    headerSlot: "header-flags",
    exportHeader: "风险标记",
    exportValue: r =>
      [
        ...(r.confined_space || []).length ? ["有限空间"] : [],
        ...safetyFlags
          .filter(f => !!r[f.key])
          .map(f => f.label)
      ].join("、")
  },
  // 自定义信息（extra jsonb 汇总为一列，不支持筛选）
  {
    key: "extra",
    label: "自定义信息",
    minWidth: 200,
    slot: "cell-extra",
    exportHeader: "自定义信息",
    exportValue: r =>
      extraEntries(r)
        .map(([k, v]) => `${k}：${v}`)
        .join("；")
  },
  // ── 有限空间详情（专页列）──
  {
    key: "confined_space_count",
    prop: "confined_space_count",
    label: "空间数量",
    width: 90,
    filterType: "text",
    filterPlaceholder: "搜索空间数量",
    formatter: r => dash(r.confined_space_count),
    exportHeader: "空间数量",
    exportValue: r => r.confined_space_count || ""
  },
  {
    key: "confined_space_type",
    label: "空间类型",
    width: 120,
    align: "center",
    // 展示改用有限空间多标签（地上/地下可并存），原文描述仍存 confined_space_type 字段
    slot: "cell-confined_space",
    filterField: "confined_space",
    filterType: "enum",
    filterOptions: confinedSpaceOptions,
    filterArrayEnum: true,
    exportHeader: "空间类型",
    exportValue: r => (r.confined_space || []).join("、")
  },
  {
    key: "confined_space_workers",
    prop: "confined_space_workers",
    label: "作业人数",
    width: 90,
    filterType: "text",
    filterPlaceholder: "搜索作业人数",
    formatter: r => dash(r.confined_space_workers),
    exportHeader: "作业人数",
    exportValue: r => r.confined_space_workers || ""
  },
  {
    key: "confined_space_hazards",
    prop: "confined_space_hazards",
    label: "主要危险因素",
    minWidth: 170,
    filterType: "text",
    filterPlaceholder: "搜索危险因素（窒息等）",
    formatter: r => dash(r.confined_space_hazards),
    exportHeader: "主要危险因素",
    exportValue: r => r.confined_space_hazards || ""
  },
  {
    key: "confined_space_op_subject",
    prop: "confined_space_op_subject",
    label: "空间作业主体",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索作业主体",
    formatter: r => dash(r.confined_space_op_subject),
    exportHeader: "空间作业主体",
    exportValue: r => r.confined_space_op_subject || ""
  },
  {
    key: "confined_space_op_cycle",
    prop: "confined_space_op_cycle",
    label: "空间作业周期",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索作业周期",
    formatter: r => dash(r.confined_space_op_cycle),
    exportHeader: "空间作业周期",
    exportValue: r => r.confined_space_op_cycle || ""
  },
  {
    key: "confined_space_fixed_workers",
    prop: "confined_space_fixed_workers",
    label: "作业人员固定",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索是否固定",
    formatter: r => dash(r.confined_space_fixed_workers),
    exportHeader: "作业人员固定",
    exportValue: r => r.confined_space_fixed_workers || ""
  },
  // ── 危化品详情（专页列）──
  {
    key: "hazchem_names",
    prop: "hazchem_names",
    label: "危化品名称",
    minWidth: 160,
    filterType: "text",
    filterPlaceholder: "搜索危化品名称",
    formatter: r => dash(r.hazchem_names),
    exportHeader: "危化品名称",
    exportValue: r => r.hazchem_names || ""
  },
  {
    key: "hazchem_op_subject",
    prop: "hazchem_op_subject",
    label: "作业主体",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索作业主体",
    formatter: r => dash(r.hazchem_op_subject),
    exportHeader: "作业主体",
    exportValue: r => r.hazchem_op_subject || ""
  },
  {
    key: "hazchem_major_hazard",
    prop: "hazchem_major_hazard",
    label: "重大危险源",
    width: 110,
    filterType: "text",
    filterPlaceholder: "搜索重大危险源",
    formatter: r => dash(r.hazchem_major_hazard),
    exportHeader: "重大危险源",
    exportValue: r => r.hazchem_major_hazard || ""
  },
  {
    key: "hazchem_state",
    prop: "hazchem_state",
    label: "危化品状态",
    width: 100,
    filterType: "text",
    filterPlaceholder: "搜索危化品状态",
    formatter: r => dash(r.hazchem_state),
    exportHeader: "危化品状态",
    exportValue: r => r.hazchem_state || ""
  },
  {
    key: "hazchem_op_cycle",
    prop: "hazchem_op_cycle",
    label: "作业周期",
    width: 110,
    filterType: "text",
    filterPlaceholder: "搜索作业周期",
    formatter: r => dash(r.hazchem_op_cycle),
    exportHeader: "作业周期",
    exportValue: r => r.hazchem_op_cycle || ""
  },
  {
    key: "hazchem_usage",
    prop: "hazchem_usage",
    label: "用途",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索用途",
    formatter: r => dash(r.hazchem_usage),
    exportHeader: "用途",
    exportValue: r => r.hazchem_usage || ""
  },
  {
    key: "hazchem_annual_usage",
    prop: "hazchem_annual_usage",
    label: "年度使用量",
    width: 110,
    filterType: "text",
    filterPlaceholder: "搜索年度使用量",
    formatter: r => dash(r.hazchem_annual_usage),
    exportHeader: "年度使用量",
    exportValue: r => r.hazchem_annual_usage || ""
  },
  {
    key: "hazchem_storage",
    prop: "hazchem_storage",
    label: "存储方式",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索存储方式",
    formatter: r => dash(r.hazchem_storage),
    exportHeader: "存储方式",
    exportValue: r => r.hazchem_storage || ""
  },
  {
    key: "hazchem_reserve",
    prop: "hazchem_reserve",
    label: "储量",
    width: 100,
    filterType: "text",
    filterPlaceholder: "搜索储量",
    formatter: r => dash(r.hazchem_reserve),
    exportHeader: "储量",
    exportValue: r => r.hazchem_reserve || ""
  },
  {
    key: "hazchem_fixed_workers",
    prop: "hazchem_fixed_workers",
    label: "固定作业人员",
    width: 120,
    filterType: "text",
    filterPlaceholder: "搜索固定作业人员",
    formatter: r => dash(r.hazchem_fixed_workers),
    exportHeader: "固定作业人员",
    exportValue: r => r.hazchem_fixed_workers || ""
  },
  // ── 小场所明细列（小场所专页展示）──
  {
    key: "venue_status",
    prop: "venue_status",
    label: "场所状态",
    width: 100,
    filterField: "venue_status",
    filterType: "text",
    filterPlaceholder: "搜索场所状态",
    formatter: r => dash(r.venue_status),
    exportHeader: "场所状态",
    exportValue: r => r.venue_status || ""
  },
  {
    key: "staff_count",
    prop: "staff_count",
    label: "从业人数",
    width: 120,
    filterField: "staff_count",
    filterType: "text",
    filterPlaceholder: "搜索从业人数",
    formatter: r => dash(r.staff_count),
    exportHeader: "从业人数",
    exportValue: r => r.staff_count || ""
  },
  {
    key: "venue_area",
    prop: "venue_area",
    label: "场所面积",
    width: 130,
    filterField: "venue_area",
    filterType: "text",
    filterPlaceholder: "搜索场所面积",
    formatter: r => dash(r.venue_area),
    exportHeader: "场所面积",
    exportValue: r => r.venue_area || ""
  },
  {
    key: "capacity",
    prop: "capacity",
    label: "承载人数",
    width: 110,
    filterField: "capacity",
    filterType: "text",
    filterPlaceholder: "搜索承载人数",
    formatter: r => dash(r.capacity),
    exportHeader: "承载人数",
    exportValue: r => r.capacity || ""
  },
  {
    key: "is_multistory",
    prop: "is_multistory",
    label: "占用多层",
    width: 100,
    filterField: "is_multistory",
    filterType: "bool",
    filterPlaceholder: "是否占用多层",
    formatter: r => (r.is_multistory === null ? "—" : r.is_multistory ? "是" : "否"),
    exportHeader: "占用多层",
    exportValue: r => (r.is_multistory === null ? "" : r.is_multistory ? "是" : "否")
  },
  {
    key: "has_business_license",
    prop: "has_business_license",
    label: "营业执照",
    width: 100,
    filterField: "has_business_license",
    filterType: "bool",
    filterPlaceholder: "有无营业执照",
    formatter: r => (r.has_business_license === null ? "—" : r.has_business_license ? "有" : "无"),
    exportHeader: "营业执照",
    exportValue: r => (r.has_business_license === null ? "" : r.has_business_license ? "有" : "无")
  },
  {
    key: "venue_category_major",
    prop: "venue_category_major",
    label: "场所大类",
    width: 120,
    filterField: "venue_category_major",
    filterType: "text",
    filterPlaceholder: "搜索场所大类",
    formatter: r => dash(r.venue_category_major),
    exportHeader: "场所大类",
    exportValue: r => r.venue_category_major || ""
  },
  {
    key: "venue_category_minor",
    prop: "venue_category_minor",
    label: "场所小类",
    width: 160,
    filterField: "venue_category_minor",
    filterType: "text",
    filterPlaceholder: "搜索场所小类",
    formatter: r => dash(r.venue_category_minor),
    exportHeader: "场所小类",
    exportValue: r => r.venue_category_minor || ""
  },
  {
    key: "is_multi_license",
    prop: "is_multi_license",
    label: "一店多证",
    width: 100,
    filterField: "is_multi_license",
    filterType: "bool",
    filterPlaceholder: "是否一店多证",
    formatter: r => (r.is_multi_license === null ? "—" : r.is_multi_license ? "是" : "否"),
    exportHeader: "一店多证",
    exportValue: r => (r.is_multi_license === null ? "" : r.is_multi_license ? "是" : "否")
  },
  {
    key: "venue_risk_level",
    prop: "venue_risk_level",
    label: "风险等级",
    width: 100,
    filterField: "venue_risk_level",
    filterType: "text",
    filterPlaceholder: "搜索风险等级",
    formatter: r => dash(r.venue_risk_level),
    exportHeader: "风险等级",
    exportValue: r => r.venue_risk_level || ""
  },
  {
    key: "venue_safety_inspector",
    prop: "venue_safety_inspector",
    label: "安全巡查员",
    width: 170,
    filterField: "venue_safety_inspector",
    filterType: "text",
    filterPlaceholder: "搜索安全巡查员",
    formatter: r => dash(r.venue_safety_inspector),
    exportHeader: "安全巡查员",
    exportValue: r => r.venue_safety_inspector || ""
  },
  {
    key: "venue_fire_instructor",
    prop: "venue_fire_instructor",
    label: "消防指导员",
    width: 170,
    filterField: "venue_fire_instructor",
    filterType: "text",
    filterPlaceholder: "搜索消防指导员",
    formatter: r => dash(r.venue_fire_instructor),
    exportHeader: "消防指导员",
    exportValue: r => r.venue_fire_instructor || ""
  },
  {
    key: "regulatory_dept",
    prop: "regulatory_dept",
    label: "专业监管部门",
    width: 240,
    filterField: "regulatory_dept",
    filterType: "text",
    filterPlaceholder: "搜索专业监管部门",
    formatter: r => dash(r.regulatory_dept),
    exportHeader: "专业监管部门及监管事项",
    exportValue: r => r.regulatory_dept || ""
  },
  {
    key: "territorial_dept",
    prop: "territorial_dept",
    label: "属地管理部门",
    width: 180,
    filterField: "territorial_dept",
    filterType: "text",
    filterPlaceholder: "搜索属地管理部门",
    formatter: r => dash(r.territorial_dept),
    exportHeader: "属地管理部门",
    exportValue: r => r.territorial_dept || ""
  },
  {
    key: "involves_evacuation",
    prop: "involves_evacuation",
    label: "疏散安全",
    width: 100,
    filterField: "involves_evacuation",
    filterType: "bool",
    filterPlaceholder: "是否涉及疏散安全",
    formatter: r => (r.involves_evacuation === null ? "—" : r.involves_evacuation ? "是" : "否"),
    exportHeader: "涉及疏散安全",
    exportValue: r => (r.involves_evacuation === null ? "" : r.involves_evacuation ? "是" : "否")
  },
  {
    key: "involves_fire_safety",
    prop: "involves_fire_safety",
    label: "消防安全",
    width: 100,
    filterField: "involves_fire_safety",
    filterType: "bool",
    filterPlaceholder: "是否涉及消防安全",
    formatter: r => (r.involves_fire_safety === null ? "—" : r.involves_fire_safety ? "是" : "否"),
    exportHeader: "涉及消防安全",
    exportValue: r => (r.involves_fire_safety === null ? "" : r.involves_fire_safety ? "是" : "否")
  },
  {
    key: "involves_electrical",
    prop: "involves_electrical",
    label: "用电安全",
    width: 100,
    filterField: "involves_electrical",
    filterType: "bool",
    filterPlaceholder: "是否涉及用电安全",
    formatter: r => (r.involves_electrical === null ? "—" : r.involves_electrical ? "是" : "否"),
    exportHeader: "涉及用电安全",
    exportValue: r => (r.involves_electrical === null ? "" : r.involves_electrical ? "是" : "否")
  },
  {
    key: "involves_lpg",
    prop: "involves_lpg",
    label: "瓶装液化气",
    width: 110,
    filterField: "involves_lpg",
    filterType: "bool",
    filterPlaceholder: "是否涉及瓶装液化气",
    formatter: r => (r.involves_lpg === null ? "—" : r.involves_lpg ? "是" : "否"),
    exportHeader: "涉及瓶装液化气",
    exportValue: r => (r.involves_lpg === null ? "" : r.involves_lpg ? "是" : "否")
  },
  {
    key: "involves_pipe_gas",
    prop: "involves_pipe_gas",
    label: "管道天然气",
    width: 110,
    filterField: "involves_pipe_gas",
    filterType: "bool",
    filterPlaceholder: "是否涉及管道天然气",
    formatter: r => (r.involves_pipe_gas === null ? "—" : r.involves_pipe_gas ? "是" : "否"),
    exportHeader: "涉及管道天然气",
    exportValue: r => (r.involves_pipe_gas === null ? "" : r.involves_pipe_gas ? "是" : "否")
  },
  {
    key: "involves_other_fuel",
    prop: "involves_other_fuel",
    label: "其他燃料",
    width: 110,
    filterField: "involves_other_fuel",
    filterType: "bool",
    filterPlaceholder: "是否涉及其他燃料",
    formatter: r => (r.involves_other_fuel === null ? "—" : r.involves_other_fuel ? "是" : "否"),
    exportHeader: "涉及其他燃料",
    exportValue: r => (r.involves_other_fuel === null ? "" : r.involves_other_fuel ? "是" : "否")
  },
  {
    key: "involves_decoration_work",
    prop: "involves_decoration_work",
    label: "装饰装修及危险作业",
    width: 140,
    filterField: "involves_decoration_work",
    filterType: "bool",
    filterPlaceholder: "是否涉及装饰装修及危险作业",
    formatter: r => (r.involves_decoration_work === null ? "—" : r.involves_decoration_work ? "是" : "否"),
    exportHeader: "涉及装饰装修及危险作业",
    exportValue: r => (r.involves_decoration_work === null ? "" : r.involves_decoration_work ? "是" : "否")
  },
  {
    key: "involves_three_in_one",
    prop: "involves_three_in_one",
    label: "三合一场所",
    width: 110,
    filterField: "involves_three_in_one",
    filterType: "bool",
    filterPlaceholder: "是否涉及三合一场所",
    formatter: r => (r.involves_three_in_one === null ? "—" : r.involves_three_in_one ? "是" : "否"),
    exportHeader: "涉及三合一场所",
    exportValue: r => (r.involves_three_in_one === null ? "" : r.involves_three_in_one ? "是" : "否")
  },
  {
    key: "involves_flammable_storage",
    prop: "involves_flammable_storage",
    label: "储存易燃易爆",
    width: 120,
    filterField: "involves_flammable_storage",
    filterType: "bool",
    filterPlaceholder: "是否涉及储存易燃易爆物品",
    formatter: r => (r.involves_flammable_storage === null ? "—" : r.involves_flammable_storage ? "是" : "否"),
    exportHeader: "涉及储存易燃易爆物品",
    exportValue: r => (r.involves_flammable_storage === null ? "" : r.involves_flammable_storage ? "是" : "否")
  }
];

/** 列显示面板分组 */
export const ENTERPRISE_COLUMN_GROUPS = [
  {
    name: "基础信息",
    keys: [
      "name",
      "code",
      "category",
      "asset",
      "industry",
      "region",
      "legal_person",
      "person_in_charge",
      "safety_officer",
      "address"
    ]
  },
  {
    name: "规模信息",
    keys: ["employee_count", "annual_output_value"]
  },
  {
    name: "安全信息",
    keys: [
      "fire_level",
      "confined_space",
      "flags",
      "confined_space_count",
      "confined_space_type",
      "confined_space_workers",
      "confined_space_hazards",
      "confined_space_op_subject",
      "confined_space_op_cycle",
      "confined_space_fixed_workers",
      "hazchem_names",
      "hazchem_op_subject",
      "hazchem_major_hazard",
      "hazchem_state",
      "hazchem_op_cycle",
      "hazchem_usage",
      "hazchem_annual_usage",
      "hazchem_storage",
      "hazchem_reserve",
      "hazchem_fixed_workers",
      // ── 小场所明细 ──
      "venue_status",
      "staff_count",
      "venue_area",
      "capacity",
      "is_multistory",
      "has_business_license",
      "venue_category_major",
      "venue_category_minor",
      "is_multi_license",
      "venue_risk_level",
      "venue_safety_inspector",
      "venue_fire_instructor",
      "regulatory_dept",
      "territorial_dept",
      "involves_evacuation",
      "involves_fire_safety",
      "involves_electrical",
      "involves_lpg",
      "involves_pipe_gas",
      "involves_other_fuel",
      "involves_decoration_work",
      "involves_three_in_one",
      "involves_flammable_storage"
    ]
  },
  { name: "自定义信息", keys: ["extra"] }
];

/**
 * 有限空间 / 危化品详细展开字段：仅在对应专页作为表格列展示，
 * 企业主表的「列显示」面板里不出现（有限空间数据仅在专页展示）。
 */
export const DETAIL_COLUMN_KEYS = [
  "confined_space",
  "confined_space_count",
  "confined_space_type",
  "confined_space_workers",
  "confined_space_hazards",
  "confined_space_op_subject",
  "confined_space_op_cycle",
  "confined_space_fixed_workers",
  "hazchem_names",
  "hazchem_op_subject",
  "hazchem_major_hazard",
  "hazchem_state",
  "hazchem_op_cycle",
  "hazchem_usage",
  "hazchem_annual_usage",
  "hazchem_storage",
  "hazchem_reserve",
  "hazchem_fixed_workers",
  // ── 小场所明细 ──
  "venue_status",
  "staff_count",
  "venue_area",
  "capacity",
  "is_multistory",
  "has_business_license",
  "venue_category_major",
  "venue_category_minor",
  "is_multi_license",
  "venue_risk_level",
  "venue_safety_inspector",
  "venue_fire_instructor",
  "regulatory_dept",
  "territorial_dept",
  "involves_evacuation",
  "involves_fire_safety",
  "involves_electrical",
  "involves_lpg",
  "involves_pipe_gas",
  "involves_other_fuel",
  "involves_decoration_work",
  "involves_three_in_one",
  "involves_flammable_storage"
];

/** 批量修改不开放的列：唯一字段 / 组合列 */
const BULK_EXCLUDE = ["name", "code", "category", "flags", "extra"];

export interface EnterpriseConfigOptions {
  /** 列偏好存储键（各页面独立，互不污染） */
  storageKey: string;
  /** 表格标题（默认 企业列表） */
  title?: string;
  /** 专页列子集（不传 = 企业主表，排除详情列） */
  columnKeys?: string[];
  /** 预设筛选（专页用，不可被重置清除） */
  presetFilter?: FilterGroup;
  /** 区域动态选项（region 列表头筛选的 options 来源） */
  regionOptions: Ref<string[]>;
}

/** 构建企业表格配置：主表与专页（有限空间/危化品）共用 */
export function buildEnterpriseConfig(
  opts: EnterpriseConfigOptions
): TablePageConfig<Enterprise> {
  const title = opts.title ?? "企业列表";

  /** 当前页可用列：专页为传入子集（保持传入顺序），主表排除详情列 */
  const availableKeys = opts.columnKeys?.length
    ? opts.columnKeys.filter(k => ENTERPRISE_COLUMNS.some(c => c.key === k))
    : ENTERPRISE_COLUMNS.map(c => c.key).filter(
        k => !DETAIL_COLUMN_KEYS.includes(k)
      );
  const columns = availableKeys
    .map(k => ENTERPRISE_COLUMNS.find(c => c.key === k))
    .filter(Boolean) as TableColumnConfig<Enterprise>[];
  // region 列的动态筛选选项（与编辑弹窗共享同一数据源）
  for (const col of columns) {
    if (col.key === "region")
      col.filterOptions = () => opts.regionOptions.value;
  }

  /** 默认可见列：专页全部可见；主表只显示基础信息组 */
  const defaultVisible = opts.columnKeys?.length
    ? availableKeys.filter(k => k !== "name") // name 为 alwaysVisible，不占勾选位
    : ENTERPRISE_COLUMN_GROUPS[0].keys.filter(
        k => availableKeys.includes(k) && k !== "name"
      );

  /** 可批量修改字段：从列定义派生（enum 且有静态选项 → select；number → number；其余 text） */
  const bulkEditFields: BulkEditField[] = columns
    .filter(c => c.filterField && c.filterType && !BULK_EXCLUDE.includes(c.key))
    .map(c => {
      const staticOpts = Array.isArray(c.filterOptions)
        ? c.filterOptions
        : null;
      const type: BulkEditField["type"] =
        c.filterType === "number"
          ? "number"
          : c.filterType === "bool"
            ? "bool"
            : c.filterType === "enum" && staticOpts
              ? "select"
              : "text";
      return {
        key: c.filterField as string,
        label: c.label,
        type,
        options:
          type === "select" && staticOpts
            ? staticOpts.map(o =>
                typeof o === "string" ? { value: o, label: o } : o
              )
            : undefined
      };
    });

  return {
    storageKey: opts.storageKey,
    title,
    fetchApi: searchEnterprisesWithFilter,
    columns,
    defaultVisible,
    columnGroups: ENTERPRISE_COLUMN_GROUPS,
    migrateColumnPrefs: saved => {
      // 旧版「法人/负责人/安全员」组合列拆分为三列
      if (saved.includes("persons")) {
        saved.push("legal_person", "person_in_charge", "safety_officer");
      }
      // 旧版「职工/年产值」组合列拆分为两列
      if (saved.includes("staff_output")) {
        saved.push("employee_count", "annual_output_value");
      }
      return saved;
    },
    keywordPlaceholder: "搜索企业名称 / 信用代码",
    presetFilter: opts.presetFilter,
    bulkEditFields,
    bulkUpdateApi: (row, payload) => updateEnterprise(row.id, payload as any),
    inlineUpdateApi: async (row, field, value) => {
      await updateEnterprise(row.id, { [field]: value } as any);
    },
    deleteApi: row => deleteEnterprise(row.id),
    deleteConfirmText: "确定要删除企业「{name}」吗？",
    batchDeleteConfirmText:
      "确定要删除选中的 {n} 家企业吗？有关联巡查单的企业会被系统拒绝删除。",
    exportFileName: title
  };
}
