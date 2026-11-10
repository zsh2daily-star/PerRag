const Layout = () => import("@/layout/index.vue");

export default {
  // 企业列表：由「企业安全生产」分组移出，作为一级菜单
  // （单子项会被侧边栏扁平化为一级菜单项）。
  path: "/enterprise-list",
  name: "EnterpriseListGroup",
  component: Layout,
  redirect: "/enterprises",
  meta: {
    icon: "ep/list",
    title: "企业列表",
    rank: 0.5
  },
  children: [
    {
      path: "/enterprises",
      name: "EnterpriseList",
      component: () => import("@/views/enterprises/index.vue"),
      meta: {
        title: "企业列表",
        keepAlive: true
      }
    }
  ]
} satisfies RouteConfigsTable;
