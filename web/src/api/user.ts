import { http } from "@/utils/http";

export type UserResult = {
  success: boolean;
  data: {
    /** 头像 */
    avatar: string;
    /** 用户名 */
    username: string;
    /** 昵称（后端 staff.name） */
    nickname: string;
    /** 是否管理员 */
    is_admin: boolean;
    /** 当前登录用户的角色 */
    roles: Array<string>;
    /** 按钮级别权限 */
    permissions: Array<string>;
    /** `token` */
    accessToken: string;
    /** 用于调用刷新`accessToken`的接口时所需的`token` */
    refreshToken: string;
    /** `accessToken`的过期时间（格式'xxxx/xx/xx xx:xx:xx'） */
    expires: Date;
  };
};

export type RefreshTokenResult = {
  success: boolean;
  data: {
    /** `token` */
    accessToken: string;
    /** 用于调用刷新`accessToken`的接口时所需的`token` */
    refreshToken: string;
    /** `accessToken`的过期时间（格式'xxxx/xx/xx xx:xx:xx'） */
    expires: Date;
  };
};

/** 当前登录用户信息（GET /auth/me 返回） */
export type MeResult = {
  success: boolean;
  data: {
    id: number;
    username: string;
    name: string;
    is_admin: boolean;
    roles: Array<string>;
    permissions: Array<string>;
  };
};

/** 登录（走真实后端 POST /auth/login） */
export const getLogin = (data?: object) => {
  return http.request<UserResult>("post", "/auth/login", { data });
};

/** 获取当前登录用户（走真实后端 GET /auth/me） */
export const getMe = () => {
  return http.request<MeResult>("get", "/auth/me");
};

/** 修改密码请求参数 */
export interface ChangePasswordData {
  old_password: string;
  new_password: string;
}

/** 修改密码（走真实后端 POST /auth/change-password） */
export const changePassword = (data: ChangePasswordData) => {
  return http.request<{ success: boolean; message: string }>(
    "post",
    "/auth/change-password",
    { data }
  );
};

/** 刷新`token`（V2 暂无 refresh 端点，保留接口以兼容模板调用） */
export const refreshTokenApi = (data?: object) => {
  return http.request<RefreshTokenResult>("post", "/refresh-token", { data });
};
