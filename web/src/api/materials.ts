import { http } from "@/utils/http";

/** 材料状态 */
export type MaterialStatus = "pending" | "indexing" | "indexed" | "failed";

/** 材料文件格式 */
export type MaterialFormat = "pdf" | "docx" | "xlsx" | "md";

/** 材料信息 */
export interface Material {
  /** 主键ID */
  id: number;
  /** UUID 唯一标识 */
  uid: string;
  /** 文件名 */
  filename: string;
  /** 文件格式 */
  file_format: MaterialFormat;
  /** 存储路径 */
  storage_path: string;
  /** 文件哈希 */
  file_hash: string;
  /** 索引状态 */
  status: MaterialStatus;
  /** 上传人ID */
  uploaded_by: number;
  /** 上传人姓名 */
  uploaded_by_name: string;
  /** 页数 */
  page_count: number;
  /** 关联企业统一社会信用代码列表 */
  enterprise_codes: string[];
  /** 适用行业 */
  scope_industry: string;
  /** 适用区域 */
  scope_region: string;
  /** 创建时间 */
  created_at: string;
}

/** 材料列表查询参数 */
export interface MaterialQuery {
  /** 页码（1-based） */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 索引状态过滤 */
  status?: MaterialStatus;
  /** 关键词（文件名/uid模糊匹配） */
  keyword?: string;
}

/** 材料范围更新请求 */
export interface MaterialScopeUpdate {
  /** 适用行业 */
  scope_industry?: string;
  /** 适用区域 */
  scope_region?: string;
  /** 关联企业代码列表 */
  enterprise_codes?: string[];
}

/** 索引证据片段 */
export interface EvidenceChunk {
  /** 页码 */
  page_number: number;
  /** 章节标题 */
  section_title: string;
  /** 文本内容 */
  text: string;
}

/** 材料列表响应 */
export type MaterialListResult = {
  success: boolean;
  data: {
    total: number;
    items: Material[];
  };
};

/** 单条材料响应 */
export type MaterialResult = {
  success: boolean;
  data: Material | null;
  message?: string;
};

/** 证据片段响应 */
export type EvidenceResult = {
  success: boolean;
  data: {
    material_uid: string;
    filename: string;
    chunks: EvidenceChunk[];
  };
  message?: string;
};

/** 通用操作响应 */
export type MaterialOpResult = {
  success: boolean;
  data?: Material;
  message?: string;
};

/** 获取材料列表 */
export const getMaterialList = (params?: MaterialQuery) => {
  return http
    .request<MaterialListResult>("get", "/materials", { params })
    .then(res => {
      const d = res.data;
      return {
        total: d.total,
        list: d.items.map(item => ({
          id: item.uid,
          filename: item.filename,
          format: item.source_format || item.file_format || "",
          status: item.status,
          pages: item.page_count || 0,
          uploader: item.uploaded_by_name || "",
          uploaded_at: item.created_at,
          scope: {
            scope_industry: item.scope_industry,
            scope_region: item.scope_region,
            enterprise_codes: item.enterprise_codes
          }
        }))
      };
    });
};

/** 获取单个材料详情 */
export const getMaterial = (uid: string) => {
  return http
    .request<MaterialResult>("get", `/materials/${uid}`)
    .then(res => res.data);
};

/** 新增材料 */
export const createMaterial = (data: Partial<Material> & { format?: string }) => {
  const payload: any = { ...data };
  if (data.format) {
    payload.file_format = data.format;
    delete payload.format;
  }
  return http
    .request<MaterialOpResult>("post", "/materials", { data: payload })
    .then(res => res.data);
};

/** 删除材料 */
export const deleteMaterial = (uid: string | number) => {
  return http
    .request<MaterialOpResult>("delete", `/materials/${uid}`)
    .then(res => res.data);
};

/** 触发材料索引 */
export const indexMaterial = (uid: string | number) => {
  return http
    .request<MaterialOpResult>("post", `/materials/${uid}/index`)
    .then(res => res.data);
};

/** 重建材料索引 */
export const reindexMaterial = (uid: string | number) => {
  return http
    .request<MaterialOpResult>("post", `/materials/${uid}/reindex`)
    .then(res => res.data);
};

/** 更新材料范围 */
export const updateMaterialScope = (
  uid: string | number,
  data: MaterialScopeUpdate
) => {
  return http
    .request<MaterialOpResult>("patch", `/materials/${uid}/scope`, {
      data
    })
    .then(res => res.data);
};

/** 获取证据片段（溯源） */
export const getEvidence = (materialUid: string | number) => {
  return http
    .request<EvidenceResult>("get", `/evidence/${materialUid}`)
    .then(res => {
      const d = res.data;
      return {
        material_uid: d.material_uid,
        filename: d.filename,
        chunks: (d.chunks || []).map((c, i) => ({
          id: i + 1,
          page_number: c.page_number,
          text: c.text
        }))
      };
    });
};

/** 获取材料列表（视图层使用的别名） */
export const getMaterials = getMaterialList;

/** 触发材料索引（视图层使用的别名） */
export const triggerIndex = indexMaterial;

/** 重建材料索引（视图层使用的别名） */
export const rebuildIndex = reindexMaterial;

/** 更新材料范围（视图层使用的别名） */
export const updateScope = updateMaterialScope;
