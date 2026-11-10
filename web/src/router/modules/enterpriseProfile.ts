const Layout = () => import("@/layout/index.vue");

export default {
  // 企业档案：一级菜单（单子项会被侧边栏扁平化为一级菜单项）。
  // 路径用单数 /enterprise 前缀，避免与后端代理前缀 /enterprises 冲突
  // （否则直接刷新该地址会被 Vite 代理转发到后端返回 JSON）。
  path: "/enterprise-profile",
  name: "EnterpriseProfileGroup",
  component: Layout,
  redirect: "/enterprise/profile",
  meta: {
    icon: "ep/postcard",
    title: "企业档案",
    rank: 0.6
  },
  children: [
    {
      path: "/enterprise/profile",
      name: "EnterpriseProfile",
      component: () => import("@/views/enterprises/profile.vue"),
      meta: {
        title: "企业档案"
      }
    }
  ]
} satisfies RouteConfigsTable;
