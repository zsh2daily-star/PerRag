<script setup lang="ts">
import { computed, ref, watch, inject } from "vue";
import { Delete } from "@element-plus/icons-vue";
import type { FilterFieldSpec, FilterLeaf as FilterLeafT, Operator } from "./types";
import { OPERATOR_LABELS } from "./types";

defineOptions({ name: "FilterLeaf" });

const props = defineProps<{
  /** 当前 leaf 状态 */
  modelValue: FilterLeafT;
  /** 可选字段规格列表 */
  fields: FilterFieldSpec[];
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: FilterLeafT): void;
  (e: "remove"): void;
}>();

/** 当前选中字段规格 */
const spec = computed<FilterFieldSpec | undefined>(() =>
  props.fields.find(f => f.key === props.modelValue.field)
);

/** 当前字段允许的 operator 列表 */
const allowedOperators = computed<Operator[]>(() =>
  spec.value ? spec.value.operators : []
);

/**
 * 是否在 UI 上显示 operator 下拉：
 *   bool / keyword 类型不需要用户手动选择 operator ——
 *   bool 固定 is_true/is_false/is_null/is_not_null；
 *   keyword 固定 like/like_not。
 *   text/number/date/enum/enum_array 才暴露给用户切换。
 */
const showOperatorSelect = computed(() => {
  if (!spec.value) return false;
  return ["text", "number", "date", "enum", "enum_array"].includes(
    spec.value.type
  );
});

/** emit 更新（深拷贝保持响应性） */
const patch = (p: Partial<FilterLeafT>) => {
  emit("update:modelValue", { ...props.modelValue, ...p });
};

/** 切换字段时，重置 operator 与 value 到字段类型默认值 */
const handleFieldChange = (key: string) => {
  const newSpec = props.fields.find(f => f.key === key);
  if (!newSpec) return;
  const defaultOp =
    (newSpec.operators?.[0] as Operator) ?? "eq";
  let defaultValue: unknown = "";
  if (newSpec.type === "bool") defaultValue = null;
  else if (newSpec.type === "enum" || newSpec.type === "enum_array")
    defaultValue = [];
  else if (newSpec.type === "number") defaultValue = null;
  emit("update:modelValue", {
    type: "leaf",
    field: key,
    operator: defaultOp,
    value: defaultValue
  });
};

/** 切换 operator 时清空 value（避免不同 op 间 value 类型不匹配） */
const handleOperatorChange = (op: Operator) => {
  const t = spec.value?.type;
  let defaultValue: unknown = "";
  if (op === "is_null" || op === "is_not_null" || op === "is_true" || op === "is_false") {
    defaultValue = null;
  } else if (
    (t === "enum" && (op === "in" || op === "not_in")) ||
    (t === "enum_array" && (op === "contains" || op === "overlaps"))
  ) {
    defaultValue = [];
  } else if (t === "bool") {
    defaultValue = null;
  } else if (t === "number") {
    defaultValue = null;
  }
  emit("update:modelValue", {
    ...props.modelValue,
    operator: op,
    value: defaultValue
  });
};

// ── enum 字段多选值变化时自动切 operator ──
// 仅在正向操作符(eq/in)间自动切换；用户手动选了 neq/not_in/is_null 就保持
const handleEnumChange = (val: string[] | string) => {
  const op = props.modelValue.operator;
  // 无值类操作符不需要自动切换
  if (isValuelessOp.value) {
    emit("update:modelValue", { ...props.modelValue, value: val });
    return;
  }
  // enum_array：operator（contains/overlaps）由用户手动选择，
  // 值始终是多标签数组，不做 eq/in 自动切换
  if (spec.value?.type === "enum_array") {
    emit("update:modelValue", {
      ...props.modelValue,
      value: Array.isArray(val) ? val : val ? [val] : []
    });
    return;
  }
  let newOp: Operator = op;
  if (Array.isArray(val)) {
    if (val.length >= 2) {
      // 多选 → in 或 not_in（保留 not_in）
      newOp = op === "not_in" ? "not_in" : "in";
    } else if (val.length === 1) {
      // 单选 → eq 或 neq（保留 neq）
      newOp = op === "neq" ? "neq" : "eq";
    }
  } else {
    // 单选下拉返回 string：eq 或 neq
    newOp = op === "neq" ? "neq" : "eq";
  }
  emit("update:modelValue", {
    ...props.modelValue,
    operator: newOp,
    value: val
  });
};

const handleTextInput = (val: string) => {
  emit("update:modelValue", { ...props.modelValue, value: val });
};

const handleNumberInput = (val: number | null) => {
  emit("update:modelValue", { ...props.modelValue, value: val });
};

const handleDateChange = (val: string | null) => {
  emit("update:modelValue", { ...props.modelValue, value: val });
};

// ── 动态选项（options_source）按需加载 + 缓存 ──
// 用 inject 共享顶层组件的 fetch 函数；如未提供则不加载（用 options 内联值）
const fetchOptions = inject<(endpoint: string) => Promise<string[]>>(
  "ReFilterBuilder_fetchOptions",
  null as unknown as (endpoint: string) => Promise<string[]>
);

const dynamicOptions = ref<string[]>([]);
const dynamicLoading = ref(false);
const dynamicLoaded = ref(false);

const effectiveOptions = computed<string[]>(() => {
  if (spec.value?.options) return spec.value.options;
  if (spec.value?.options_source && dynamicLoaded.value) {
    return dynamicOptions.value;
  }
  return [];
});

const loadDynamicOptions = async () => {
  if (
    !spec.value?.options_source ||
    dynamicLoaded.value ||
    dynamicLoading.value ||
    !fetchOptions
  ) {
    return;
  }
  dynamicLoading.value = true;
  try {
    dynamicOptions.value = await fetchOptions(spec.value.options_source);
    dynamicLoaded.value = true;
  } finally {
    dynamicLoading.value = false;
  }
};

watch(
  () => spec.value?.options_source,
  src => {
    dynamicLoaded.value = false;
    dynamicOptions.value = [];
    if (src) loadDynamicOptions();
  },
  { immediate: true }
);

/** enum 字段是否多选模式：
 *  - enum：op 是 in/not_in 时多选；eq/neq 时单选
 *  - enum_array：contains/overlaps 均接收数组，恒为多选 */
const enumMultiple = computed(() => {
  if (spec.value?.type === "enum_array") return true;
  return (
    props.modelValue.operator === "in" ||
    props.modelValue.operator === "not_in"
  );
});

/** 是否是"无值"类操作符 */
const isValuelessOp = computed(() => {
  return ["is_null", "is_not_null", "is_true", "is_false"].includes(
    props.modelValue.operator
  );
});
</script>

<template>
  <div class="filter-leaf">
    <!-- 字段选择 -->
    <el-select
      :model-value="modelValue.field"
      placeholder="选择字段"
      style="width: 150px"
      @change="handleFieldChange"
    >
      <el-option
        v-for="f in fields"
        :key="f.key"
        :label="f.label"
        :value="f.key"
      />
    </el-select>

    <!-- operator 下拉（仅 text/number/date 显示） -->
    <el-select
      v-if="showOperatorSelect"
      :model-value="modelValue.operator"
      style="width: 110px; margin-left: 8px"
      @change="handleOperatorChange"
    >
      <el-option
        v-for="op in allowedOperators"
        :key="op"
        :label="OPERATOR_LABELS[op]"
        :value="op"
      />
    </el-select>

    <!-- 值控件：按字段类型切换 -->
    <div class="value-box" style="margin-left: 8px">
      <!-- 无值 op：不渲染值控件 -->
      <span v-if="isValuelessOp" class="valueless-hint" />

      <!-- keyword / text 输入框 -->
      <el-input
        v-else-if="spec && (spec.type === 'text' || spec.type === 'keyword')"
        :model-value="String(modelValue.value ?? '')"
        placeholder="输入关键词"
        style="width: 180px"
        @input="handleTextInput"
      />

      <!-- enum 下拉（单选/多选自动切换） -->
      <el-select
        v-else-if="spec && (spec.type === 'enum' || spec.type === 'enum_array')"
        :model-value="modelValue.value as string[] | string"
        :multiple="enumMultiple"
        :collapse-tags="true"
        :collapse-tags-tooltip="true"
        :loading="dynamicLoading"
        placeholder="选择值"
        style="width: 180px"
        @change="handleEnumChange"
      >
        <el-option
          v-for="opt in effectiveOptions"
          :key="opt"
          :label="opt"
          :value="opt"
        />
      </el-select>

      <!-- bool 固定两项：is_true/is_false 已通过 operator 下拉选择，
           这里不显示额外值控件 -->
      <span v-else-if="spec && spec.type === 'bool'" class="valueless-hint" />

      <!-- number 数字输入 -->
      <el-input-number
        v-else-if="spec && spec.type === 'number'"
        :model-value="(modelValue.value as number | null) ?? null"
        :controls="false"
        style="width: 140px"
        @change="handleNumberInput"
      />

      <!-- date 日期选择 -->
      <el-date-picker
        v-else-if="spec && spec.type === 'date'"
        :model-value="(modelValue.value as string | null) ?? null"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="选择日期"
        style="width: 160px"
        @change="handleDateChange"
      />
    </div>

    <!-- 删除按钮 -->
    <el-button
      :icon="Delete"
      circle
      size="small"
      style="margin-left: 8px"
      @click="emit('remove')"
    />
  </div>
</template>

<style scoped>
.filter-leaf {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.valueless-hint {
  display: inline-block;
  width: 1px;
  height: 24px;
}
</style>
