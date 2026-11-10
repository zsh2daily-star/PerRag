import { http } from "@/utils/http";
import type { StaffBriefResult } from "./staff";

/** 审计日志操作状态 */
export type AuditStatus = "success" | "failed";

/** 审计日志 */
export interface AuditLog {
  /** 主键ID */
  id: number;
  /** 操作人ID */
  staff_id: number;
  /** 操作人姓名 */
  staff_name: string;
  /** 操作类型：login/logout/create/update/delete/query */
  action: string;
  /** 资源类型 */
  resource_type: string;
  /** 资源ID */
  resource_id: string;
  /** 操作状态 */
  status: AuditStatus;
  /** 请求耗时（毫秒） */
  latency_ms: number;
  /** 请求IP */
  ip: string;
  /** 操作时间 */
  created_at: string;
  /** 失败原因（仅 failed 记录可能有） */
  error_message?: string;
}

/** 审计日志列表查询参数 */
export interface AuditQuery {
  /** 页码（1-based） */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 操作类型过滤 */
  action?: string;
  /** 员工ID过滤 */
  staff_id?: number;
  /** 起始日期（YYYY-MM-DD） */
  start_date?: string;
  /** 截止日期（YYYY-MM-DD） */
  end_date?: string;
}

/** 审计日志列表响应 */
export type AuditListResult = {
  success: boolean;
  data: {
    total: number;
    items: AuditLog[];
  };
};

/** 获取审计日志列表 */
export const getAuditLogs = (query?: any) => {
  const params: any = { ...query };
  if (query?.staff !== undefined) {
    params.staff_id = query.staff;
    delete params.staff;
  }
  return http
    .request<AuditListResult>("get", "/admin/audit-logs", {
      params
    })
    .then(res => {
      const d = res.data;
      return {
        total: d.total,
        list: d.items.map(item => ({
          id: item.id,
          created_at: item.created_at,
          operator: item.staff_name,
          action: item.action,
          resource_type: item.resource_type,
          resource_id: item.resource_id,
          status: item.status,
          duration_ms: item.latency_ms,
          ip: item.ip,
          error_message: (item as any).error_message
        }))
      };
    });
};

/** 获取审计操作类型选项 */
export const getAuditActions = () => {
  return http
    .request<any>("get", "/admin/audit-logs/actions")
    .then(res => {
      const actions = res.data || [];
      return actions.map((a: string) => ({ label: a, value: a }));
    });
};

/** 获取审计员工下拉选项 */
export const getAuditStaffOptions = () => {
  return http
    .request<StaffBriefResult>("get", "/admin/staff/brief")
    .then(res => {
      const d = res.data;
      return (d || []).map(item => ({
        label: item.name,
        value: String(item.id)
      }));
    });
};
