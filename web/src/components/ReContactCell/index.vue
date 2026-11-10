<!--
  联系方式单元格：姓名上 + 电话下灰色小字两行显示。
  企业列表 / 厂中厂 共用，统一 4 表「姓名 + 联系方式」列的表达。

  - 无 phone 仅显示 person（缺失则 fallback）
  - 有 phone 时在第二行用灰色小字展示
-->
<template>
  <div class="contact-cell">
    <div class="contact-cell__person">{{ person || fallback }}</div>
    <div v-if="phone" class="contact-cell__phone">{{ phone }}</div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: "ReContactCell" });

withDefaults(
  defineProps<{
    person?: string | null;
    phone?: string | null;
    fallback?: string;
  }>(),
  {
    person: "",
    phone: "",
    fallback: "—"
  }
);
</script>

<style scoped>
.contact-cell {
  line-height: 1.5;
}
.contact-cell__phone {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
