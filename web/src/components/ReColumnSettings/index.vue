<!--
  列显示设置面板（el-popover 内容）—— 企业列表 / 巡查台账共用。

  有 groups 时按分组渲染并支持"全选本组"；无 groups 时平铺全部列。
  勾选状态由父级 useColumnPrefs 提供，本组件只负责渲染。
-->
<template>
  <div class="re-column-panel">
    <div class="font-bold mb-2">勾选要显示的列</div>
    <div class="re-column-panel__body">
      <!-- 分组模式 -->
      <template v-if="groups.length">
        <template v-for="g in groups" :key="g">
          <div class="re-column-group__title">
            <span>{{ g }}</span>
            <el-button
              link
              type="primary"
              size="small"
              class="re-column-group__action"
              @click="$emit('toggle-group', g)"
            >
              {{ groupFullySelected(g) ? "清空本组" : "全选本组" }}
            </el-button>
          </div>
          <el-checkbox-group
            :model-value="visibleKeys"
            class="re-column-group"
            @update:model-value="onValueChange"
          >
            <el-checkbox
              v-for="col in columnsOfGroup(g)"
              :key="col.key"
              :value="col.key"
              >{{ col.label }}</el-checkbox
            >
          </el-checkbox-group>
        </template>
      </template>
      <!-- 平铺模式 -->
      <el-checkbox-group
        v-else
        :model-value="visibleKeys"
        class="re-column-group"
        @update:model-value="onValueChange"
      >
        <el-checkbox v-for="col in columns" :key="col.key" :value="col.key">
          {{ col.label }}
        </el-checkbox>
      </el-checkbox-group>
    </div>
    <div class="re-column-actions">
      <el-button link type="primary" size="small" @click="$emit('select-all')">
        全选
      </el-button>
      <el-button link type="primary" size="small" @click="$emit('clear-all')">
        取消全选
      </el-button>
      <el-button link type="primary" size="small" @click="$emit('invert')">
        反选
      </el-button>
      <span class="text-xs text-gray-400 ml-auto">
        已选 {{ visibleKeys.length }}/{{ columns.length }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
export interface ColumnSettingItem {
  key: string;
  label: string;
  group?: string;
}

const props = withDefaults(
  defineProps<{
    columns: ColumnSettingItem[];
    visibleKeys: string[];
    /** 分组标题顺序（不传 = 平铺） */
    groups?: string[];
  }>(),
  { groups: () => [] }
);

const emit = defineEmits<{
  (e: "update:visibleKeys", v: string[]): void;
  (e: "change", v: string[]): void;
  (e: "select-all"): void;
  (e: "clear-all"): void;
  (e: "invert"): void;
  (e: "toggle-group", group: string): void;
}>();

const columnsOfGroup = (g: string) => props.columns.filter(c => c.group === g);

const groupFullySelected = (g: string) => {
  const keys = columnsOfGroup(g).map(c => c.key);
  return keys.length > 0 && keys.every(k => props.visibleKeys.includes(k));
};

const onValueChange = (v: string[]) => {
  emit("update:visibleKeys", v);
  emit("change", v);
};
</script>

<style scoped>
.re-column-panel__body {
  max-height: 46vh;
  overflow-y: auto;
}
.re-column-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin-bottom: 6px;
}
.re-column-group :deep(.el-checkbox) {
  margin-right: 0;
}
.re-column-group__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: bold;
  font-size: 13px;
  margin: 8px 0 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.re-column-group__title:first-child {
  border-top: none;
  margin-top: 0;
}
.re-column-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}
</style>
