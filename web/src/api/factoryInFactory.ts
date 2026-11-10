import { http } from "@/utils/http";
import type { FilterGroup } from "@/components/ReFilterBuilder/types";

/** 厂中厂专项整治记录（出租方-承租方租赁关系） */
export interface FactoryInFactory {
  id: number;
  /** 检查批次（如 "2026-厂中厂消防"） */
  inspection_batch: string;
  /** 出租方企业ID（外键，可空） */
  lessor_enterprise_id: number | null;
  /** 出租方名称（外键展示名 or 原文） */
  lessor_name: string | null;
  /** 承租方企业ID（外键，可空） */
  tenant_enterprise_id: number | null;
  /** 承租方名称 */
  tenant_name: string | null;
  /** 县市区 */
  region: string | null;
  /** 具体地址 */
  address: string | null;
  // ── 出租方字段 ──
  lessor_contact_person: string | null;
  lessor_contact_phone: string | null;
  /** 出租方使用面积（原文，可能含单位） */
  lessor_use_area: string | null;
  /** 出租方隐患描述（多条以顿号分隔） */
  lessor_hazard_description: string | null;
  /** 出租方整改状态 */
  lessor_rect_status: string | null;
  // ── 承租方字段 ──
  /** 承租面积（数字，平方米） */
  tenant_area: number | null;
  tenant_contact_person: string | null;
  tenant_contact_phone: string | null;
  /** 是否签订协议 */
  agreement_signed: boolean | null;
  tenant_hazard_description: string | null;
  tenant_rect_status: string | null;
  // ── 排查信息 ──
  /** 场所类别 */
  site_category: string | null;
  /** 排查日期 YYYY-MM-DD（后端返回 ISO 字符串） */
  inspection_date: string | null;
  /** 排查人员 */
  inspectors: string | null;
  /** 督改人员 */
  rect_supervisor: string | null;
  remark: string | null;
  created_at: string;
  updated_at: string;
}

/** 列表查询参数（旧版 GET 端点用，保留兼容） */
export interface FactoryInFactoryQuery {
  page: number;
  page_size: number;
  keyword?: string;
  inspection_batch?: string;
  region?: string;
  lessor_name?: string;
  tenant_name?: string;
  site_category?: string;
  inspection_date?: string;
}

/** 列表响应 */
export type FactoryInFactoryListResult = {
  success: boolean;
  data: {
    total: number;
    items: FactoryInFactory[];
  };
};

/** 单条响应 */
export type FactoryInFactoryResult = {
  success: boolean;
  data: FactoryInFactory | null;
  message?: string;
};

/** 通用操作响应 */
export type FactoryInFactoryOpResult = {
  success: boolean;
  data?: FactoryInFactory;
  message?: string;
};

/** 获取厂中厂列表（旧版 GET 端点，保留兼容） */
export const getFactoryInFactoryList = (params?: FactoryInFactoryQuery) => {
  return http
    .request<FactoryInFactoryListResult>("get", "/factory-in-factory", { params })
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};

/** 获取单条记录 */
export const getFactoryInFactory = (id: number) => {
  return http
    .request<FactoryInFactoryResult>("get", `/factory-in-factory/${id}`)
    .then(res => res.data);
};

/** 新建记录 */
export const createFactoryInFactory = (data: Partial<FactoryInFactory>) => {
  return http
    .request<FactoryInFactoryOpResult>("post", "/factory-in-factory", { data })
    .then(res => res.data);
};

/** 更新记录 */
export const updateFactoryInFactory = (
  idOrForm: number | (Partial<FactoryInFactory> & { id: number }),
  data?: Partial<FactoryInFactory>
) => {
  const id =
    typeof idOrForm === "number" ? idOrForm : (idOrForm as any).id;
  const payload =
    typeof idOrForm === "number" ? data : (idOrForm as Partial<FactoryInFactory>);
  return http
    .request<FactoryInFactoryOpResult>("put", `/factory-in-factory/${id}`, {
      data: payload
    })
    .then(res => res.data);
};

/** 删除记录 */
export const deleteFactoryInFactory = (id: number) => {
  return http.request<FactoryInFactoryOpResult>(
    "delete",
    `/factory-in-factory/${id}`
  );
};

/** 导入厂中厂专项整治责任清单 Excel（幂等：同批次同承租方重复导入→更新） */
export const importFactoryInFactoryExcel = (file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return http
    .request<{ success: boolean; data: any }>(
      "post",
      "/factory-in-factory/import",
      { data: formData, headers: { "Content-Type": "multipart/form-data" } }
    )
    .then(res => res.data);
};

/** 全部检查批次（前端下拉用） */
export const getFactoryInFactoryBatches = () => {
  return http
    .request<{ success: boolean; data: string[] }>(
      "get",
      "/factory-in-factory/batches"
    )
    .then(res => res.data);
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

/** 获取厂中厂表筛选字段元数据（GET /factory-in-factory/meta/filter-fields） */
export const getFactoryInFactoryFilterFields = () => {
  return http
    .request<{ success: boolean; data: any[] }>(
      "get",
      "/factory-in-factory/meta/filter-fields"
    )
    .then(res => res.data as any[]);
};

/** 复合条件查询请求体 */
export interface FactoryInFactoryFilterQuery {
  filter: FilterGroup;
  page: number;
  page_size: number;
}

/** 复合条件查询（POST /factory-in-factory/query） */
export const searchFactoryInFactoryWithFilter = (
  filter: FilterGroup,
  page: number = 1,
  page_size: number = 20
) => {
  return http
    .request<{
      success: boolean;
      data: { total: number; items: FactoryInFactory[] };
    }>("post", "/factory-in-factory/query", {
      data: { filter, page, page_size } as FactoryInFactoryFilterQuery
    })
    .then(res => {
      const d = res.data;
      return { list: d.items, total: d.total };
    });
};
