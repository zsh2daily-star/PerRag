const Layout = () => import("@/layout/index.vue");

export default {
  path: "/enterprise",
  name: "Enterprises",
  component: Layout,
  redirect: "/enterprises/confined-spaces",
  meta: {
    icon: "ep/office-building",
    title: "企业安全生产",
    rank: 2
  },
  children: [
    {
      path: "/enterprises/confined-spaces",
      name: "ConfinedSpaceList",
      component: () => import("@/views/confined-spaces/index.vue"),
      meta: {
        title: "有限空间管理",
        keepAlive: true
      }
    },
    {
      path: "/enterprises/hazardous-chemicals",
      name: "HazardousChemicalList",
      component: () => import("@/views/hazardous-chemicals/index.vue"),
      meta: {
        title: "危化品管理",
        keepAlive: true
      }
    },
    {
      path: "/enterprises/small-venues",
      name: "SmallVenueList",
      component: () => import("@/views/small-venues/index.vue"),
      meta: {
        title: "小场所管理",
        keepAlive: true
      }
    },
    {
      path: "/enterprises/factory-in-factory",
      name: "FactoryInFactoryList",
      component: () => import("@/views/factoryInFactory/index.vue"),
      meta: {
        title: "厂中厂整治",
        keepAlive: true
      }
    },
    {
      // 巡查整改单：由原独立顶级模块并入「企业安全生产」分组
      path: "/inspections",
      name: "InspectionList",
      component: () => import("@/views/inspections/index.vue"),
      meta: {
        title: "巡查整改单",
        keepAlive: true
      }
    }
  ]
} satisfies RouteConfigsTable;
