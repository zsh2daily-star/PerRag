import { http } from "@/utils/http";

/** 员工账号 */
export interface Staff {
  /** 主键ID */
  id: number;
  /** 用户名 */
  username: string;
  /** 姓名 */
  name: string;
  /** 部门 */
  department: string;
  /** 职位 */
  position: string;
  /** 电话 */
  phone: string;
  /** 是否管理员 */
  is_admin: boolean;
  /** 是否启用 */
  is_active: boolean;
  /** 最近登录时间 */
  last_login_at: string | null;
  /** 创建时间 */
  created_at: string;
  /** 每日对话费用上限（元），null=用系统默认；管理员不受限 */
  chat_daily_limit_yuan: number | null;
  /** 对话功能是否被管理员手动锁定 */
  chat_locked: boolean;
}

/** 员工简要信息（用于下拉选择器） */
export interface StaffBrief {
  id: number;
  name: string;
}

/** 员工列表查询参数 */
export interface StaffQuery {
  /** 页码（1-based） */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 关键词（姓名/用户名/部门模糊匹配） */
  keyword?: string;
}

/** 创建员工响应（含初始密码） */
export interface CreateStaffResult {
  staff: Staff;
  initial_password: string;
}

/** 员工列表响应 */
export type StaffListResult = {
  success: boolean;
  data: {
    total: number;
    items: Staff[];
  };
};

/** 单条员工响应 */
export type StaffResult = {
  success: boolean;
  data: Staff | null;
  message?: string;
};

/** 创建员工响应 */
export type CreateStaffResponse = {
  success: boolean;
  data: CreateStaffResult;
};

/** 重置密码响应 */
export type ResetPasswordResult = {
  success: boolean;
  data: {
    new_password: string;
  };
  message?: string;
};

/** 简要列表响应 */
export type StaffBriefResult = {
  success: boolean;
  data: StaffBrief[];
};

/** 获取员工列表 */
export const getStaffList = (params?: StaffQuery) => {
  return http
    .request<StaffListResult>("get", "/admin/staff", { params })
    .then(res => {
      const d = res.data;
      return {
        total: d.total,
        list: d.items.map(item => ({
          ...item,
          last_login: item.last_login_at
        }))
      };
    });
};

/** 创建员工账号 */
export const createStaff = (data: Partial<Staff>) => {
  return http
    .request<CreateStaffResponse>("post", "/admin/staff", { data })
    .then(res => {
      const d = res.data;
      return {
        staff: d.staff,
        password: d.initial_password
      };
    });
};

/** 更新员工信息 */
export const updateStaff = (
  idOrForm: number | (Partial<Staff> & { id: number }),
  data?: Partial<Staff>
) => {
  const id =
    typeof idOrForm === "number" ? idOrForm : (idOrForm as any).id;
  const payload =
    typeof idOrForm === "number" ? data : (idOrForm as Partial<Staff>);
  return http
    .request<StaffResult>("put", `/admin/staff/${id}`, { data: payload })
    .then(res => res.data);
};

/** 重置员工密码 */
export const resetStaffPassword = (id: number) => {
  return http
    .request<ResetPasswordResult>(
      "post",
      `/admin/staff/${id}/reset-password`
    )
    .then(res => {
      const d = res.data;
      return { password: d.new_password };
    });
};

/** 停用员工账号 */
export const deactivateStaff = (id: number) => {
  return http
    .request<StaffResult>("post", `/admin/staff/${id}/deactivate`)
    .then(res => res.data);
};

/** 获取员工简要列表（下拉） */
export const getStaffBrief = () => {
  return http
    .request<StaffBriefResult>("get", "/admin/staff/brief")
    .then(res => res.data);
};

/** 获取员工列表（视图层使用的别名） */
export const getStaff = getStaffList;

/** 重置员工密码（视图层使用的别名） */
export const resetPassword = resetStaffPassword;

/** 切换员工启用状态（视图层使用的别名） */
export const toggleStatus = deactivateStaff;

/** 更新员工对话配置（限额 / 锁定状态） */
export const updateStaffChatSettings = (
  id: number,
  data: { chat_daily_limit_yuan?: number | null; chat_locked?: boolean }
) => {
  return http
    .request<StaffResult>("put", `/admin/staff/${id}/chat-settings`, { data })
    .then(res => res.data);
};
