const Layout = () => import("@/layout/index.vue");

export default {
  path: "/system",
  name: "System",
  component: Layout,
  meta: {
    icon: "ep/setting",
    title: "系统管理",
    rank: 5
  },
  children: [
    {
      path: "/system/staff",
      name: "StaffManage",
      component: () => import("@/views/system/staff/index.vue"),
      meta: {
        title: "账号管理",
        roles: ["admin"]
      }
    },
    {
      path: "/system/audit",
      name: "AuditLog",
      component: () => import("@/views/system/audit/index.vue"),
      meta: {
        title: "审计日志",
        roles: ["admin"]
      }
    }
  ]
} satisfies RouteConfigsTable;
