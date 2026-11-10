<script setup lang="ts">
/**
 * 小场所专页
 * 复用企业列表同一套表格基座，预设筛选 enterprise_category @> ['小场所']，
 * 并把小场所相关明细列（场所状态/从业人员/场所面积/承载/多层/营业执照/大类/小类/...）
 * 作为表头展示，与有限空间专页同套模式。
 */
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import EnterprisesView from "@/views/enterprises/index.vue";

defineOptions({ name: "SmallVenues" });

// 预设筛选：enterprise_category 数组列包含「小场所」
const presetFilter: FilterGroup = {
  type: "group",
  logic: "AND",
  children: [
    {
      type: "leaf",
      field: "enterprise_category",
      operator: "contains",
      value: ["小场所"]
    }
  ]
};

/** 专页预设列：顺序即默认列序，全部可见 */
const columnKeys = [
  "name",
  "venue_status",
  "staff_count",
  "venue_area",
  "capacity",
  "is_multistory",
  "has_business_license",
  "venue_category_major",
  "venue_category_minor",
  "is_multi_license",
  "venue_risk_level",
  "person_in_charge",
  "safety_officer",
  "venue_safety_inspector",
  "venue_fire_instructor",
  "regulatory_dept",
  "territorial_dept",
  "involves_evacuation",
  "involves_fire_safety",
  "involves_electrical",
  "involves_lpg",
  "involves_pipe_gas",
  "involves_other_fuel",
  "involves_decoration_work",
  "involves_three_in_one",
  "involves_flammable_storage"
];
</script>

<template>
  <EnterprisesView
    :preset-filter="presetFilter"
    :column-keys="columnKeys"
    column-storage-key="small-venues-columns-visible"
    column-order-storage-key="small-venues-columns-order"
  />
</template>
