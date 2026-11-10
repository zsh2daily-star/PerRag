import { http } from "@/utils/http";
import type { FilterGroup } from "@/components/ReFilterBuilder/types";

/** 危化品使用企业专项整治记录（一行一种危化品） */
export interface HazardousChemical {
  id: number;
  /** 企业ID（外键，可空） */
  enterprise_id: number | null;
  /** 企业名称（来自 Enterprise 主表） */
  enterprise_name: string | null;
  /** 县（市、区）（来自 Enterprise 主表） */
  enterprise_region: string | null;
  /** 镇（区、街道） */
  town: string | null;
  /** 行业类型（来自 Enterprise 主表） */
  enterprise_industry: string | null;
  // ── 危化品明细 ──
  /** 危化品名称 */
  hazchem_name: string | null;
  /** 危化品状态（液态/固态/气态） */
  hazchem_state: string | null;
  /** 储量（原文，如 "20t"） */
  reserve: string | null;
  /** 设计最大储量（原文） */
  max_reserve: string | null;
  /** 年度使用量（原文） */
  annual_usage: string | null;
  /** 危化品用途 */
  usage: string | null;
  /** 存储方式 */
  storage_method: string | null;
  /** 存储位置 */
  storage_location: string | null;
  /** 是否构成重大危险源 */
  major_hazard: string | null;
  /** 备注 */
  remark: string | null;
  created_at: string;
  updated_at: string;
}

/** 列表查询参数（旧版 GET 端点用，保留兼容） */
export interface HazardousChemicalQuery {
  page: number;
  page_size: number;
  keyword?: string;
  region?: string;
  enterprise_name?: string;
  hazchem_name?: string;
  hazchem_state?: string;
  major_hazard?: string;
}

/** 列表响应 */
export type HazardousChemicalListResult = {
  success: boolean;
  data: {
    total: number;
    items: HazardousChemical[];
  };
};

/** 单条响应 */
export type HazardousChemicalResult = {
  success: boolean;
  data: HazardousChemical | null;
  message?: string;
};

/** 通用操作响应 */
export type HazardousChemicalOpResult = {
  success: boolean;
  data?: HazardousChemical;
  message?: string;
};

/** 获取危化品列表（旧版 GET 端点，保留兼容） */
export const getHazardousChemicalsList = (params?: HazardousChemicalQuery) => {
  return http
    .request<HazardousChemicalListResult>("get", "/hazardous-chemicals", {
      params
    })
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};

/** 获取单条记录 */
export const getHazardousChemical = (id: number) => {
  return http
    .request<HazardousChemicalResult>("get", `/hazardous-chemicals/${id}`)
    .then(res => res.data);
};

/** 新建记录 */
export const createHazardousChemical = (data: Partial<HazardousChemical>) => {
  return http
    .request<HazardousChemicalOpResult>("post", "/hazardous-chemicals", {
      data
    })
    .then(res => res.data);
};

/** 更新记录 */
export const updateHazardousChemical = (
  idOrForm: number | (Partial<HazardousChemical> & { id: number }),
  data?: Partial<HazardousChemical>
) => {
  const id = typeof idOrForm === "number" ? idOrForm : (idOrForm as any).id;
  const payload =
    typeof idOrForm === "number"
      ? data
      : (idOrForm as Partial<HazardousChemical>);
  return http
    .request<HazardousChemicalOpResult>(
      "put",
      `/hazardous-chemicals/${id}`,
      { data: payload }
    )
    .then(res => res.data);
};

/** 删除记录 */
export const deleteHazardousChemical = (id: number) => {
  return http.request<HazardousChemicalOpResult>(
    "delete",
    `/hazardous-chemicals/${id}`
  );
};

// ── 动态筛选条件构建器：类型与 API ─────────────────────────
export type {
  Operator,
  FieldType,
  FilterFieldSpec,
  FilterLeaf,
  FilterGroup,
  FilterNode
} from "@/components/ReFilterBuilder/types";

/** 获取危化品表筛选字段元数据（GET /hazardous-chemicals/meta/filter-fields） */
export const getHazardousChemicalFilterFields = () => {
  return http
    .request<{ success: boolean; data: any[] }>(
      "get",
      "/hazardous-chemicals/meta/filter-fields"
    )
    .then(res => res.data as any[]);
};

/** 复合条件查询请求体 */
export interface HazardousChemicalFilterQuery {
  filter: FilterGroup;
  page: number;
  page_size: number;
}

/** 复合条件查询（POST /hazardous-chemicals/query） */
export const searchHazardousChemicalsWithFilter = (
  filter: FilterGroup,
  page: number = 1,
  page_size: number = 20
) => {
  return http
    .request<{
      success: boolean;
      data: { total: number; items: HazardousChemical[] };
    }>("post", "/hazardous-chemicals/query", {
      data: { filter, page, page_size } as HazardousChemicalFilterQuery
    })
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};
