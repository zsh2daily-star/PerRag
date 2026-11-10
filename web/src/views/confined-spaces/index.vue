<script setup lang="ts">
/**
 * 有限空间管理页面
 * 复用企业列表同一套表格基座，预设筛选 confined_space IS NOT NULL，
 * 并将有限空间相关明细列（地上/地下、数量、类型、作业人数、危险因素）作为表头
 */
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import EnterprisesView from "@/views/enterprises/index.vue";

defineOptions({ name: "ConfinedSpaces" });

const presetFilter: FilterGroup = {
  type: "group",
  logic: "AND",
  children: [
    {
      type: "leaf",
      field: "confined_space",
      operator: "is_not_null",
      value: null
    }
  ]
};

/** 专页预设列：顺序即默认列序，全部可见（空间类型列已改为多标签展示，不再单列有限空间） */
const columnKeys = [
  "name",
  "region",
  "confined_space_count",
  "confined_space_type",
  "confined_space_workers",
  "confined_space_hazards",
  "confined_space_op_subject",
  "confined_space_op_cycle",
  "confined_space_fixed_workers",
  "person_in_charge",
  "safety_officer"
];
</script>

<template>
  <EnterprisesView
    :preset-filter="presetFilter"
    :column-keys="columnKeys"
    column-storage-key="confined-spaces-columns-visible"
    column-order-storage-key="confined-spaces-columns-order"
  />
</template>
