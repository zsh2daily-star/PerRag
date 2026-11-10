const Layout = () => import("@/layout/index.vue");

export default {
  path: "/material",
  name: "Materials",
  component: Layout,
  redirect: "/materials",
  meta: {
    icon: "ep/document",
    title: "材料管理",
    rank: 3
  },
  children: [
    {
      path: "/materials",
      name: "MaterialList",
      component: () => import("@/views/materials/index.vue"),
      meta: {
        title: "材料列表",
        keepAlive: true
      }
    }
  ]
} satisfies RouteConfigsTable;
