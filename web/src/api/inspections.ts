import { http } from "@/utils/http";
import { getToken, formatToken } from "@/utils/auth";
import type { EnterpriseListResult } from "./enterprises";
import type { StaffBriefResult } from "./staff";

/** 隐患等级 */
export type HazardLevel = "low" | "medium" | "high";

/** 检查记录状态 */
export type RecordStatus = "open" | "rectifying" | "closed";

/** 检查记录 */
export interface InspectionRecord {
  /** 主键ID */
  id: number;
  /** 检查批次号 */
  inspection_batch: string;
  /** 企业ID */
  enterprise_id: number;
  /** 企业统一社会信用代码 */
  enterprise_code: string;
  /** 企业名称 */
  enterprise_name: string;
  /** 检查标题 */
  title: string;
  /** 创建时间 */
  created_at: string;
  /** 检查日期 */
  inspection_date: string;
  /** 检查地点 */
  location: string;
  /** 检查人员ID */
  inspector_user_id: number;
  /** 检查人员姓名 */
  inspector_name: string;
  /** 隐患等级 */
  hazard_level: HazardLevel;
  /** 隐患类别 */
  hazard_category: string;
  /** 隐患描述 */
  hazard_description: string;
  /** 隐患序号（同企业同批次内） */
  hazard_index: number;
  /** 发现隐患总数 */
  hazard_count_total: number;
  /** 整改责任人ID */
  rect_responsible_user_id: number;
  /** 整改责任人姓名 */
  rect_responsible_name: string;
  /** 交办单编号（同批次共享） */
  notice_number: string | null;
  /** 整改期限 */
  rect_deadline: string;
  /** 整改完成时间 */
  rect_completed_at: string | null;
  /** 复查日期（完成整改时键入，精确到日） */
  review_date: string | null;
  /** 状态 */
  status: RecordStatus;
  /** 园区 */
  region: string;
  /** 行业 */
  industry: string;
  /** 检查人（多人顿号分隔，Excel 原文） */
  inspectors: string;
  /** 备注 */
  remark: string;
}

/** 检查记录列表查询参数 */
export interface InspectionQuery {
  /** 页码（1-based） */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 状态过滤 */
  status?: RecordStatus;
  /** 企业ID过滤 */
  enterprise_id?: number;
  /** 检查批次精确匹配（一次到点巡查可能覆盖多企业，返回该批次全部隐患） */
  inspection_batch?: string;
  /** 关键词（标题/企业名称/隐患描述模糊匹配） */
  keyword?: string;
}

/** 检查记录统计数据 */
export interface InspectionStats {
  /** 总数 */
  total: number;
  /** 待整改数 */
  open_count: number;
  /** 整改中数 */
  rectifying_count: number;
  /** 已关闭数 */
  closed_count: number;
  /** 逾期数 */
  overdue_count: number;
  /** 隐患等级分布 */
  hazard_level_distribution: {
    low: number;
    medium: number;
    high: number;
  };
}

/** 检查记录列表响应 */
export type InspectionListResult = {
  success: boolean;
  data: {
    total: number;
    items: InspectionRecord[];
  };
};

/** 单条检查记录响应 */
export type InspectionResult = {
  success: boolean;
  data: InspectionRecord | null;
  message?: string;
};

/** 统计数据响应 */
export type InspectionStatsResult = {
  success: boolean;
  data: InspectionStats;
};

/** 后端记录 → 前端行模型（GET 列表与 POST /query 共用，不重复映射） */
const mapInspectionItem = (item: any) => {
  return {
    id: item.id,
    title: item.title,
    enterprise: item.enterprise_name,
    enterprise_id: item.enterprise_id,
    enterprise_code: item.enterprise_code,
    inspection_batch: item.inspection_batch,
    notice_number: item.notice_number ?? null,
    hazard_index: item.hazard_index,
    hazard_count_total: item.hazard_count_total,
    inspect_date: item.inspection_date,
    level: item.hazard_level,
    status: item.status,
    deadline: item.rect_deadline,
    description: item.hazard_description,
    category: item.hazard_category,
    location: item.location,
    region: item.region,
    industry: item.industry,
    inspectors: item.inspector_name,
    inspector_user_id: item.inspector_user_id ?? null,
    responsible: item.rect_responsible_name,
    rect_responsible_user_id: item.rect_responsible_user_id ?? null,
    completed_at: item.rect_completed_at,
    review_date: item.review_date,
    remark: item.remark,
    overdue: isOverdue(item.status, item.rect_deadline)
  };
};

/** 超期 = 未闭环且整改期限早于今天（按日期比较，避免时分秒时区误差） */
const isOverdue = (status: string | null, deadline: string | null) => {
  if (status === "closed" || !deadline) return false;
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
  return String(deadline).slice(0, 10) < today;
};

/** 获取检查记录列表 */
export const getInspectionList = (params?: InspectionQuery) => {
  return http
    .request<InspectionListResult>("get", "/inspection-records", {
      params
    })
    .then(res => {
      const d = res.data;
      return { total: d.total, list: d.items.map(mapInspectionItem) };
    });
};

/** 巡查表筛选字段元数据（与企业表同一套协议） */
export const getInspectionFilterFields = () => {
  return http
    .request<{ success: boolean; data: any[] }>(
      "get",
      "/inspection-records/meta/filter-fields"
    )
    .then(res => res.data as any[]);
};

/** 复合条件查询（POST /inspection-records/query，与企业表同一套筛选协议） */
export const searchInspectionsWithFilter = (
  filter: Record<string, unknown>,
  page: number = 1,
  page_size: number = 20
) => {
  return http
    .request<{ success: boolean; data: { total: number; items: any[] } }>(
      "post",
      "/inspection-records/query",
      { data: { filter, page, page_size } }
    )
    .then(res => ({
      total: res.data.total,
      list: res.data.items.map(mapInspectionItem)
    }));
};

/** 获取单条检查记录 */
export const getInspection = (id: number) => {
  return http
    .request<InspectionResult>("get", `/inspection-records/${id}`)
    .then(res => res.data);
};

/** 新增检查记录 */
export const createInspection = (data: any) => {
  const payload: any = { ...data };
  if (data.inspect_date) {
    payload.inspection_date = data.inspect_date;
    delete payload.inspect_date;
  }
  if (data.inspector_id !== undefined) {
    payload.inspector_user_id = data.inspector_id;
    delete payload.inspector_id;
  }
  if (data.level !== undefined) {
    payload.hazard_level = data.level;
    delete payload.level;
  }
  if (data.category !== undefined) {
    payload.hazard_category = data.category;
    delete payload.category;
  }
  if (data.description !== undefined) {
    payload.hazard_description = data.description;
    delete payload.description;
  }
  if (data.assignee_id !== undefined) {
    payload.rect_responsible_user_id = data.assignee_id;
    delete payload.assignee_id;
  }
  if (data.deadline !== undefined) {
    payload.rect_deadline = data.deadline;
    delete payload.deadline;
  }
  return http
    .request<InspectionResult>("post", "/inspection-records", {
      data: payload
    })
    .then(res => res.data);
};

/** 更新检查记录（用于状态流转） */
export const updateInspection = (
  id: number,
  data: Partial<InspectionRecord>
) => {
  return http
    .request<InspectionResult>("put", `/inspection-records/${id}`, {
      data
    })
    .then(res => res.data);
};

/** 获取检查记录统计 */
export const getInspectionStats = () => {
  return http
    .request<InspectionStatsResult>(
      "get",
      "/inspection-records/stats"
    )
    .then(res => res.data);
};

/** 获取检查记录列表（视图层使用的别名） */
export const getInspections = getInspectionList;

/** 获取单条检查记录（视图层使用的别名） */
export const getInspectionDetail = (id: number) => {
  return http
    .request<InspectionResult>("get", `/inspection-records/${id}`)
    .then(res => {
      const r = res.data;
      if (!r) return { timeline: [] };
      const timeline: Array<{ label: string; time: string; type: string }> = [
        {
          label: `创建检查记录（批次：${r.inspection_batch}）`,
          time: r.created_at,
          type: "primary"
        },
        {
          label: `现场巡查：${r.location}（巡查人：${r.inspector_name}）`,
          time: r.inspection_date,
          type: "info"
        },
        {
          label: `隐患识别：${r.hazard_category}（${r.hazard_level}）`,
          time: r.inspection_date,
          type: "warning"
        }
      ];
      if (r.rect_responsible_name) {
        timeline.push({
          label: `派发整改：${r.rect_responsible_name}（期限：${r.rect_deadline}）`,
          time: r.rect_deadline,
          type: "warning"
        });
      }
      if (r.status === "closed" && r.rect_completed_at) {
        timeline.push({
          label: "完成整改闭环",
          time: r.rect_completed_at,
          type: "success"
        });
      }
      return { timeline };
    });
};

/**
 * 完成整改闭环 —— 键入实际闭环日期（可能不是当天），
 * 后端将该日期同时写入整改完成时间与复查日期。
 */
export const completeRectification = (id: number, completedDate: string) => {
  return http
    .request<InspectionResult>("put", `/inspection-records/${id}`, {
      data: {
        status: "closed",
        rect_completed_at: completedDate
      }
    })
    .then(res => res.data);
};

/** 导入隐患汇总表 Excel */
export const importInspectionExcel = (file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return http
    .request<{ success: boolean; data: any }>(
      "post",
      "/inspection-records/import",
      { data: formData, headers: { "Content-Type": "multipart/form-data" } }
    )
    .then(res => res.data);
};

/** 企业下拉选项 */
export const getEnterpriseOptions = () => {
  return http
    .request<EnterpriseListResult>("get", "/enterprises", {
      params: { page: 1, page_size: 1000 }
    })
    .then(res => {
      const d = res.data;
      return (d.items || []).map(item => ({
        id: item.id,
        name: item.name
      }));
    });
};

/** 未关联企业档案的隐患分组 */
export interface UnmatchedGroup {
  enterprise_name: string;
  record_count: number;
  batches: { batch: string; count: number }[];
}

export const getUnmatchedEnterprises = () => {
  return http
    .request<{ success: boolean; data: UnmatchedGroup[] }>(
      "get",
      "/inspection-records/unmatched-enterprises"
    )
    .then(res => res.data);
};

/** relink 结果（保留完整响应体，success/message 在最外层） */
export type RelinkResult = {
  success: boolean;
  code?: string;
  message?: string;
  data: {
    updated?: number;
    enterprise_name?: string;
    conflict_batches?: { batch: string; count: number }[];
  };
};

/** 把未关联企业名下的记录统一关联到目标企业档案 */
export const relinkEnterprise = (data: {
  enterprise_name: string;
  enterprise_id: number;
  inspection_batch?: string;
}) => {
  return http.request<RelinkResult>(
    "post",
    "/inspection-records/relink-enterprise",
    { data }
  );
};

/** 检查人员下拉选项 */
export const getInspectorOptions = () => {
  return http
    .request<StaffBriefResult>("get", "/admin/staff/brief")
    .then(res => res.data);
};

// ── 文书导出（按模板填充生成 .docx）─────────────────────────

/** 文书导出字段检查结果 */
export interface ExportCheckResult {
  /** 缺失字段名列表（空数组=可导出） */
  missing: string[];
  /** 是否字段齐全可导出 */
  ready: boolean;
}

/** 检查交办单导出所需字段是否齐全 */
export const checkNoticeExport = (id: number) => {
  return http
    .request<{ success: boolean; data: ExportCheckResult }>(
      "get",
      `/doc-export/notice/check/${id}`
    )
    .then(res => res.data);
};

/** 检查复查意见书导出所需字段是否齐全 */
export const checkReviewExport = (id: number) => {
  return http
    .request<{ success: boolean; data: ExportCheckResult }>(
      "get",
      `/doc-export/review/check/${id}`
    )
    .then(res => res.data);
};

/**
 * 通用 docx 下载：用 fetch 带 token，处理错误响应与文件名。
 *
 * 不走 axios http 工具：拦截器会把 blob 当 JSON 解析，
 * 错误响应也需区分 JSON 错误体与正常 blob。
 *
 * customName 非空时优先用它作为下载文件名（覆盖后端 Content-Disposition）。
 */
async function downloadDocx(
  url: string,
  fallbackName: string,
  customName?: string
) {
  const tokenInfo = getToken();
  const headers: Record<string, string> = {};
  if (tokenInfo?.accessToken) {
    headers["Authorization"] = formatToken(tokenInfo.accessToken);
  }
  const resp = await fetch(url, { headers });
  if (!resp.ok) {
    let msg = `导出失败（${resp.status}）`;
    try {
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("application/json")) {
        const err = await resp.json();
        msg = err.detail || err.message || msg;
      } else {
        const t = await resp.text();
        if (t) msg = t;
      }
    } catch {
      /* 忽略解析失败 */
    }
    throw new Error(msg);
  }
  // 文件名优先级：用户自定义 > Content-Disposition > fallback
  let filename = customName || fallbackName;
  if (!customName) {
    const cd = resp.headers.get("content-disposition") || "";
    const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)/i);
    if (m) {
      try {
        filename = decodeURIComponent(m[1]);
      } catch {
        /* 保留 fallbackName */
      }
    }
  }
  const blob = await resp.blob();

  // 优先用 File System Access API 弹"另存为"系统对话框，让用户自选保存路径
  // （Chrome/Edge 支持；Firefox/Safari 不支持则回退普通下载到浏览器默认目录）
  const w = window as any;
  if (typeof w.showSaveFilePicker === "function") {
    try {
      const handle = await w.showSaveFilePicker({
        suggestedName: filename,
        types: [
          {
            description: "Word 文档",
            accept: {
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                [".docx"]
            }
          }
        ]
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return;
    } catch (e: any) {
      // 用户点了"取消"：不报错、不回退，直接中止导出
      if (e?.name === "AbortError") {
        throw new Error("已取消保存");
      }
      // 其他异常（如安全策略拒绝）回退普通下载
    }
  }

  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(u);
}

/** 导出交办单 .docx（customName 非空时用它作为下载文件名） */
export const downloadNoticeDoc = (
  id: number,
  enterpriseName: string,
  customName?: string
) =>
  downloadDocx(
    `/doc-export/notice/${id}`,
    `交办单-${enterpriseName || id}.docx`,
    customName
  );

/** 导出复查意见书 .docx（customName 非空时用它作为下载文件名） */
export const downloadReviewDoc = (
  id: number,
  enterpriseName: string,
  customName?: string
) =>
  downloadDocx(
    `/doc-export/review/${id}`,
    `复查意见书-${enterpriseName || id}.docx`,
    customName
  );
