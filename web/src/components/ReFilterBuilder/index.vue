<script setup lang="ts">
import { ref, provide } from "vue";
import { Search, RefreshLeft } from "@element-plus/icons-vue";
import type { FilterFieldSpec, FilterGroup } from "./types";
import { makeGroup } from "./types";
import FilterGroupVue from "./FilterGroup.vue";

defineOptions({ name: "ReFilterBuilder" });

const props = defineProps<{
  /** 字段元数据（由父页 onMounted 时 fetch 后传入） */
  fields: FilterFieldSpec[];
  /** 双向绑定的根组 */
  modelValue: FilterGroup;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: FilterGroup): void;
  (e: "search"): void;
  (e: "reset"): void;
}>();

/** patch 根组 */
const patchRoot = (p: Partial<FilterGroup>) => {
  emit("update:modelValue", { ...props.modelValue, ...p });
};

/** 搜索：触发父级 fetchList */
const handleSearch = () => {
  emit("search");
};

/** 重置：清空根组 children */
const handleReset = () => {
  emit("update:modelValue", makeGroup("AND"));
  emit("reset");
};

// ── options_source 动态枚举值的按需加载与缓存 ──
const optionsCache = ref<Record<string, string[]>>({});
const pendingFetches = new Map<string, Promise<string[]>>();

/** fetch 单个 endpoint 的值列表（带缓存） */
const fetchOptions = async (endpoint: string): Promise<string[]> => {
  if (optionsCache.value[endpoint]) {
    return optionsCache.value[endpoint];
  }
  if (pendingFetches.has(endpoint)) {
    return pendingFetches.get(endpoint)!;
  }
  const p = (async () => {
    try {
      const { http } = await import("@/utils/http");
      const res = await http.request<{
        success: boolean;
        data: string[];
      }>("get", endpoint);
      const arr = res?.data ?? [];
      optionsCache.value[endpoint] = arr;
      return arr;
    } catch (e) {
      console.warn("ReFilterBuilder: load options failed", endpoint, e);
      optionsCache.value[endpoint] = [];
      return [];
    } finally {
      pendingFetches.delete(endpoint);
    }
  })();
  pendingFetches.set(endpoint, p);
  return p;
};

provide("ReFilterBuilder_fetchOptions", fetchOptions);
</script>

<template>
  <div class="re-filter-builder">
    <!-- 根组（横向 flex-wrap 铺开） -->
    <FilterGroupVue
      :model-value="modelValue"
      :fields="fields"
      :depth="0"
      @update:model-value="patchRoot"
    />

    <!-- 搜索/重置按钮（最右） -->
    <div class="actions">
      <el-button
        type="primary"
        :icon="Search"
        size="default"
        @click="handleSearch"
      >
        搜索
      </el-button>
      <el-button :icon="RefreshLeft" size="default" @click="handleReset">
        重置
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.re-filter-builder {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0;
}
.actions {
  display: flex;
  gap: 8px;
  margin-left: 12px;
  flex-shrink: 0;
  padding-top: 2px;
}
</style>
