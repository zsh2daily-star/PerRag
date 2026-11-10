import { http } from "@/utils/http";

/** 仪表盘最近材料项 */
export interface DashboardMaterial {
  /** 材料UID */
  uid: string;
  /** 文件名 */
  filename: string;
  /** 文件格式 */
  file_format: string;
  /** 索引状态 */
  status: string;
  /** 上传人姓名 */
  uploaded_by_name: string;
  /** 创建时间 */
  created_at: string;
}

/** 仪表盘逾期项 */
export interface DashboardOverdue {
  /** 检查记录ID */
  id: number;
  /** 企业名称 */
  enterprise_name: string;
  /** 检查标题 */
  title: string;
  /** 隐患等级 */
  hazard_level: string;
  /** 隐患类别 */
  hazard_category: string;
  /** 整改期限 */
  rect_deadline: string;
  /** 整改责任人 */
  rect_responsible_name: string;
  /** 状态 */
  status: string;
}

/** 仪表盘统计数据 */
export interface DashboardStats {
  /** 待整改隐患数 */
  open_count: number;
  /** 逾期隐患数 */
  overdue_count: number;
  /** 本月新增检查记录数 */
  monthly_new: number;
  /** 隐患等级分布 */
  hazard_level_distribution: {
    low: number;
    medium: number;
    high: number;
  };
  /** 最近上传材料 */
  recent_materials: DashboardMaterial[];
  /** 逾期隐患列表 */
  overdue_list: DashboardOverdue[];
}

/** 仪表盘响应 */
export type DashboardResult = {
  success: boolean;
  data: DashboardStats;
};

/** 获取仪表盘统计数据 */
export const getDashboardStats = () => {
  return http
    .request<DashboardResult>("get", "/dashboard/stats")
    .then(res => {
      const d = res.data;
      const now = Date.now();
      return {
        unclosed: d.open_count,
        overdue: d.overdue_count,
        month_new: d.monthly_new,
        material_total: d.material_total,
        level_distribution: d.hazard_level_distribution,
        overdue_list: (d.overdue_list || []).map(item => {
          const deadlineMs = new Date(item.rect_deadline).getTime();
          const overdue_days = isNaN(deadlineMs)
            ? 0
            : Math.max(
                0,
                Math.ceil((now - deadlineMs) / (1000 * 60 * 60 * 24))
              );
          return {
            id: item.id,
            title: item.title,
            enterprise: item.enterprise_name,
            deadline: item.rect_deadline,
            overdue_days
          };
        }),
        recent_materials: (d.recent_materials || []).map((item, i) => ({
          id: i + 1,
          filename: item.filename,
          format: item.file_format,
          status: item.status,
          uploaded_at: item.created_at
        }))
      };
    });
};
