import { http } from "@/utils/http";

/** 企业类型多标签（一家企业可同时是劳密+厂中厂出租等） */
export type EnterpriseCategory =
  | "劳密"
  | "厂中厂出租"
  | "厂中厂承租"
  | "小场所"
  | "规上"
  | "搬离"
  | "其他";
/** 企业规模（规上/规下） */
export type EnterpriseScale = "规上" | "规下" | "";
/** 消防重点单位等级（二级/三级/重点单位） */
export type FireLevel = "二级" | "三级" | "重点单位" | "";
/** 资产属性（韩资/台资/其他） */
export type AssetAttribute = "韩资" | "台资" | "其他" | "";
/** 有限空间位置多标签（一家企业可同时有地上+地下，空数组=未填报） */
export type ConfinedSpace = "地上" | "地下";

/** 企业信息 */
export interface Enterprise {
  /** 主键ID */
  id: number;
  /** 企业名称 */
  name: string;
  /** 18位统一社会信用代码 */
  code: string;
  /** 行业类型（固定字典：纺织/机械/建材/轻工/化工/其他） */
  industry: string;
  /** 所在区域 */
  region: string;
  /** 详细地址 */
  address: string;
  /** 法定代表人 */
  legal_person: string;
  /** 企业负责人（实际经营管理） */
  person_in_charge: string;
  /** 企业安全员 */
  safety_officer: string;
  /** 法人电话 */
  legal_person_phone: string;
  /** 企业负责人电话 */
  person_in_charge_phone: string;
  /** 安全员电话 */
  safety_officer_phone: string;
  /** 企业类型多标签：劳密/厂中厂出租/厂中厂承租/小场所/其他（空数组=未填报） */
  enterprise_category: EnterpriseCategory[];
  /** 资产属性：韩资/台资/其他 */
  asset_attribute: AssetAttribute;
  /** 企业规模：规上/规下 */
  enterprise_scale: EnterpriseScale;
  /** 职工人数（可空） */
  employee_count: number | null;
  /** 年产值（万元，可空） */
  annual_output_value: number | null;
  /** 消防重点单位等级：二级/三级/重点单位 */
  fire_level: FireLevel;
  /** 有限空间位置多标签：地上/地下（空数组=未填报） */
  confined_space: ConfinedSpace[];
  /** 有限空间数量 */
  confined_space_count: string;
  /** 有限空间类型（详细，如 地上、地下及封闭空间） */
  confined_space_type: string;
  /** 有限空间作业人数 */
  confined_space_workers: string;
  /** 有限空间危险因素 */
  confined_space_hazards: string;
  /** 有限空间作业主体（自行/发包等） */
  confined_space_op_subject: string;
  /** 有限空间作业周期 */
  confined_space_op_cycle: string;
  /** 有限空间作业人员是否固定（是/否） */
  confined_space_fixed_workers: string;
  /** 危化品名称（多种以、分隔） */
  hazchem_names: string;
  /** 危化品作业主体（自行/发包/自行和发包） */
  hazchem_op_subject: string;
  /** 是否重大危险源 */
  hazchem_major_hazard: string;
  /** 危化品状态（液态/固态/气态） */
  hazchem_state: string;
  /** 危化品作业周期（月/年） */
  hazchem_op_cycle: string;
  /** 危化品用途 */
  hazchem_usage: string;
  /** 年度使用量 */
  hazchem_annual_usage: string;
  /** 存储方式 */
  hazchem_storage: string;
  /** 危化品储量 */
  hazchem_reserve: string;
  /** 作业人员是否固定 */
  hazchem_fixed_workers: string;
  /** 天然气 */
  has_natural_gas: boolean;
  /** 注塑企业 */
  is_injection_molding: boolean;
  /** 宿舍 */
  has_dormitory: boolean;
  /** 危化品 */
  has_hazardous_chemicals: boolean;
  /** 危废品 */
  has_hazardous_waste: boolean;
  /** 粉尘爆炸 */
  involves_dust_explosion: boolean;
  /** 新能源电焊 */
  is_new_energy_welding: boolean;
  /** 大型储罐 */
  has_large_storage_tank: boolean;
  // ── 小场所明细 ──
  /** 场所状态（在业/停业） */
  venue_status: string;
  /** 从业人数（范围档位） */
  staff_count: string;
  /** 场所面积（范围档位） */
  venue_area: string;
  /** 承载人数（范围档位） */
  capacity: string;
  /** 是否占用多层（三态：null/true/false） */
  is_multistory: boolean | null;
  /** 有无营业执照（三态） */
  has_business_license: boolean | null;
  /** 场所大类 */
  venue_category_major: string;
  /** 场所小类 */
  venue_category_minor: string;
  /** 是否一店多证（三态） */
  is_multi_license: boolean | null;
  /** 专业监管部门及监管事项 */
  regulatory_dept: string;
  /** 属地管理部门 */
  territorial_dept: string;
  /** 小场所安全巡查员及联系方式 */
  venue_safety_inspector: string;
  /** 小场所消防指导员及联系方式 */
  venue_fire_instructor: string;
  /** 小场所风险等级（蓝/黄/橙/红） */
  venue_risk_level: string;
  /** 涉及疏散安全（三态） */
  involves_evacuation: boolean | null;
  /** 涉及消防安全（三态） */
  involves_fire_safety: boolean | null;
  /** 涉及用电安全（三态） */
  involves_electrical: boolean | null;
  /** 涉及瓶装液化气（三态） */
  involves_lpg: boolean | null;
  /** 涉及管道天然气（三态） */
  involves_pipe_gas: boolean | null;
  /** 涉及其他燃料（三态） */
  involves_other_fuel: boolean | null;
  /** 涉及装饰装修及危险作业（三态） */
  involves_decoration_work: boolean | null;
  /** 涉及三合一场所（三态） */
  involves_three_in_one: boolean | null;
  /** 涉及储存易燃易爆物品（三态） */
  involves_flammable_storage: boolean | null;
  /** 非新能源园区企业（台账核对标记，待人工复核删除） */
  out_of_park: boolean;
  /** 非园区判定依据（所属片区，如 光电园/韩资园/步凤镇） */
  out_of_park_note: string;
  /** 长尾自定义字段（jsonb 键值对）：单值为字符串，多值字段为字符串数组 */
  extra: Record<string, string | string[]>;
  /** 创建时间 */
  created_at: string;
  /** 更新时间 */
  updated_at: string;
}

/** 企业列表查询参数 */
export interface EnterpriseQuery {
  /** 页码（1-based） */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 关键词（企业名称/统一社会信用代码模糊匹配） */
  keyword?: string;
  /** 行业筛选 */
  industry?: string;
  /** 区域筛选 */
  region?: string;
  /** 企业类型筛选（多值，含任一即命中） */
  enterprise_category?: string[];
  /** 企业规模筛选 */
  enterprise_scale?: string;
  /** 消防等级筛选 */
  fire_level?: string;
  /** 有限空间筛选（多值，含任一即命中） */
  confined_space?: string[];
  confined_space_count?: string;
  confined_space_type?: string;
  confined_space_workers?: string;
  confined_space_hazards?: string;
  hazchem_names?: string;
  hazchem_op_subject?: string;
  hazchem_major_hazard?: string;
  hazchem_state?: string;
  hazchem_op_cycle?: string;
  hazchem_usage?: string;
  hazchem_annual_usage?: string;
  hazchem_storage?: string;
  hazchem_reserve?: string;
  hazchem_fixed_workers?: string;
  // ── 小场所明细（核心筛选字段）──
  venue_status?: string;
  venue_category_major?: string;
  venue_category_minor?: string;
  venue_risk_level?: string;
  /** 风险标记筛选（传 true = 仅显示有此标记的企业） */
  has_natural_gas?: boolean;
  is_injection_molding?: boolean;
  has_dormitory?: boolean;
  has_hazardous_chemicals?: boolean;
  has_hazardous_waste?: boolean;
  involves_dust_explosion?: boolean;
  is_new_energy_welding?: boolean;
  has_large_storage_tank?: boolean;
}

/** 企业列表响应 */
export type EnterpriseListResult = {
  success: boolean;
  data: {
    total: number;
    items: Enterprise[];
  };
};

/** 单条企业响应 */
export type EnterpriseResult = {
  success: boolean;
  data: Enterprise | null;
  message?: string;
};

/** 通用操作响应 */
export type EnterpriseOpResult = {
  success: boolean;
  data?: Enterprise;
  message?: string;
};

/** 获取企业列表 */
export const getEnterpriseList = (params?: EnterpriseQuery) => {
  return http
    .request<EnterpriseListResult>("get", "/enterprises", { params })
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};

/** 获取单个企业详情 */
export const getEnterprise = (id: number) => {
  return http
    .request<EnterpriseResult>("get", `/enterprises/${id}`)
    .then(res => res.data);
};

// ── 企业档案（介绍页）聚合数据 ──────────────────────────────
/** 关联巡查记录（介绍页展示用） */
export interface ProfileInspection {
  id: number;
  inspection_batch: string;
  title: string | null;
  inspection_date: string | null;
  inspector_name: string | null;
  hazard_level: string | null;
  hazard_category: string | null;
  hazard_description: string;
  location: string | null;
  status: string;
  rect_deadline: string | null;
  rect_responsible_name: string | null;
  notice_number: string | null;
  rect_completed_at: string | null;
  review_date: string | null;
  remark: string | null;
}

/** 关联厂中厂关系行（后端返回的原始字段，角色/相对方等在前端 computed 派生） */
export interface ProfileFactoryRelation {
  id: number;
  inspection_batch: string;
  inspection_date: string | null;
  site_category: string | null;
  lessor_enterprise_id: number | null;
  lessor_name: string | null;
  lessor_contact_person: string | null;
  lessor_contact_phone: string | null;
  lessor_use_area: string | null;
  lessor_hazard_description: string | null;
  lessor_rect_status: string | null;
  tenant_enterprise_id: number | null;
  tenant_name: string | null;
  tenant_contact_person: string | null;
  tenant_contact_phone: string | null;
  tenant_area: number | null;
  tenant_hazard_description: string | null;
  tenant_rect_status: string | null;
  agreement_signed: string | null;
  address: string | null;
  rect_supervisor: string | null;
}

/** 关联危化品明细行 */
export interface ProfileHazardousChemical {
  id: number;
  hazchem_name: string | null;
  hazchem_state: string | null;
  reserve: string | null;
  max_reserve: string | null;
  annual_usage: string | null;
  usage: string | null;
  storage_method: string | null;
  storage_location: string | null;
  major_hazard: string | null;
  remark: string | null;
  scale: string | null;
  industry: string | null;
  town: string | null;
}

/** 相关安全材料/文件（按 enterprise_codes 匹配） */
export interface ProfileMaterial {
  id: number;
  uid: string;
  filename: string;
  source_format: string | null;
  page_count: number | null;
  status: string;
  created_at: string;
}

/** 企业档案（介绍页）聚合数据 */
export interface EnterpriseProfile {
  enterprise: Enterprise;
  inspections: ProfileInspection[];
  factory_relations: ProfileFactoryRelation[];
  hazardous_chemicals: ProfileHazardousChemical[];
  materials: ProfileMaterial[];
}

export type EnterpriseProfileResult = {
  success: boolean;
  data: EnterpriseProfile | null;
  message?: string;
};

/** 获取企业档案（介绍页聚合数据） */
export const getEnterpriseProfile = (id: number) => {
  return http
    .request<EnterpriseProfileResult>("get", `/enterprises/${id}/profile`)
    .then(res => res.data);
};

/** 新增企业 */
export const createEnterprise = (data: Partial<Enterprise>) => {
  return http
    .request<EnterpriseOpResult>("post", "/enterprises", { data })
    .then(res => res.data);
};

/** 更新企业 */
export const updateEnterprise = (
  idOrForm: number | (Partial<Enterprise> & { id: number }),
  data?: Partial<Enterprise>
) => {
  // 视图层可能以 updateEnterprise(form) 形式调用（form 内含 id）
  const id =
    typeof idOrForm === "number" ? idOrForm : (idOrForm as any).id;
  const payload =
    typeof idOrForm === "number" ? data : (idOrForm as Partial<Enterprise>);
  return http
    .request<EnterpriseOpResult>("put", `/enterprises/${id}`, {
      data: payload
    })
    .then(res => res.data);
};

/** 删除企业 */
export const deleteEnterprise = (id: number) => {
  return http.request<EnterpriseOpResult>("delete", `/enterprises/${id}`);
};

/** 自定义字段历史可选值：{ 字段名: [已出现过的值...] } */
export type EnterpriseExtraOptions = Record<string, string[]>;

/** 获取自定义字段的历史可选值（多值字段下拉建议） */
export const getEnterpriseExtraOptions = () => {
  return http
    .request<{
      success: boolean;
      data: EnterpriseExtraOptions;
    }>("get", "/enterprises/meta/extra-options")
    .then(res => res.data);
};

/** 获取企业列表（视图层使用的别名） */
export const getEnterprises = getEnterpriseList;

/** 获取已有行业列表（筛选用） */
export const getEnterpriseIndustries = () => {
  return http
    .request<{ success: boolean; data: string[] }>(
      "get",
      "/enterprises/meta/industries"
    )
    .then(res => res.data);
};

/** 获取已有区域列表（筛选用） */
export const getEnterpriseRegions = () => {
  return http
    .request<{ success: boolean; data: string[] }>(
      "get",
      "/enterprises/meta/regions"
    )
    .then(res => res.data);
};

// ── 动态筛选条件构建器：类型与 API ─────────────────────────
// 数据结构与后端 app/services/filter_service.py 的 Pydantic 模型一致。
// 类型从公共组件库导出，避免双份定义漂移。
export type {
  Operator,
  FieldType,
  FilterFieldSpec,
  FilterLeaf,
  FilterGroup,
  FilterNode
} from "@/components/ReFilterBuilder/types";

import type { FilterGroup as FilterGroupT } from "@/components/ReFilterBuilder/types";

/** 获取企业表筛选字段元数据（GET /enterprises/meta/filter-fields） */
export const getEnterpriseFilterFields = () => {
  return http
    .request<{ success: boolean; data: any[] }>(
      "get",
      "/enterprises/meta/filter-fields"
    )
    .then(res => res.data as any[]); // 返回 any[] 让组件层校验
};

/** 复合条件查询请求体 */
export interface EnterpriseFilterQuery {
  filter: FilterGroupT;
  page: number;
  page_size: number;
}

/** 复合条件查询（POST /enterprises/query）
 *
 * 替代旧的 getEnterpriseList（GET，只支持固定 AND）。
 * 保留 getEnterpriseList 用于向后兼容场景。
 */
export const searchEnterprisesWithFilter = (
  filter: FilterGroupT,
  page: number = 1,
  page_size: number = 20
) => {
  return http
    .request<{ success: boolean; data: { total: number; items: Enterprise[] } }>(
      "post",
      "/enterprises/query",
      { data: { filter, page, page_size } as EnterpriseFilterQuery }
    )
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};
