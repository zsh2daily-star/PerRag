<!--
  表头筛选组件 —— 企业列表 / 巡查台账共用。

  用法（放在 el-table-column 的 #header 插槽里）：
    <ReFilterHeader :filters="hf" field="name" type="text" label="名称"
                    placeholder="搜索企业名称" />
    <ReFilterHeader :filters="hf" field="status" type="enum" label="状态"
                    :options="[{ value:'open', label:'待处理' }]" />
    <ReFilterHeader :filters="hf" field="inspection_date" type="date" label="检查日期" />

  filters 由 useHeaderFilters(filterRef, onApply) 工厂产生；
  本组件只负责 UI，条件拼装/查询触发全部在 composable 中。
-->
<template>
  <div class="th-filter">
    <span>{{ label }}</span>
    <el-popover
      :width="type === 'enum' ? 220 : 240"
      placement="bottom"
      trigger="click"
      popper-class="re-filter-header-popper"
    >
      <template #reference>
        <el-icon class="th-filter__icon" :class="{ 'is-active': active }">
          <Filter />
        </el-icon>
      </template>

      <div class="th-filter__panel">
        <!-- text/number/date 共用「为空 / 不为空」快捷入口（enum 走复选框，bool 无空值语义） -->
        <div v-if="type !== 'enum' && type !== 'bool'" class="th-filter__nullness">
          <el-link
            type="primary"
            :underline="false"
            :class="{ 'is-active': filters.nullMode(field) === 'null' }"
            @click="filters.applyNullness(field, 'null')"
          >
            为空
          </el-link>
          <el-divider direction="vertical" />
          <el-link
            type="primary"
            :underline="false"
            :class="{ 'is-active': filters.nullMode(field) === 'not_null' }"
            @click="filters.applyNullness(field, 'not_null')"
          >
            不为空
          </el-link>
        </div>

        <!-- text -->
        <template v-if="type === 'text'">
          <el-input
            :model-value="filters.textValue(field)"
            size="small"
            :placeholder="placeholder || `筛选${label}`"
            clearable
            @update:model-value="v => filters.setTextValue(field, v)"
            @keyup.enter="filters.applyTextFilter(field)"
          />
          <div class="th-filter__btns">
            <el-button size="small" @click="filters.clearTextFilter(field)">清除</el-button>
            <el-button size="small" type="primary" @click="filters.applyTextFilter(field)">筛选</el-button>
          </div>
        </template>

        <!-- number -->
        <template v-else-if="type === 'number'">
          <div class="th-filter__range">
            <el-input-number
              :model-value="numRange.min === '' ? undefined : Number(numRange.min)"
              size="small"
              controls-position="right"
              :placeholder="`最小`"
              class="th-filter__num"
              @update:model-value="v => numRange.min = v === undefined ? '' : String(v)"
            />
            <span class="th-filter__sep">~</span>
            <el-input-number
              :model-value="numRange.max === '' ? undefined : Number(numRange.max)"
              size="small"
              controls-position="right"
              placeholder="最大"
              class="th-filter__num"
              @update:model-value="v => numRange.max = v === undefined ? '' : String(v)"
            />
          </div>
          <div class="th-filter__btns">
            <el-button size="small" @click="filters.clearNumberFilter(field)">清除</el-button>
            <el-button size="small" type="primary" @click="filters.applyNumberFilter(field)">筛选</el-button>
          </div>
        </template>

        <!-- date -->
        <template v-else-if="type === 'date'">
          <el-date-picker
            :model-value="dtRange.min || undefined"
            size="small"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="开始日期"
            class="th-filter__date"
            @update:model-value="(v: string) => (dtRange.min = v || '')"
          />
          <el-date-picker
            :model-value="dtRange.max || undefined"
            size="small"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="结束日期"
            class="th-filter__date"
            @update:model-value="(v: string) => (dtRange.max = v || '')"
          />
          <div class="th-filter__btns">
            <el-button size="small" @click="filters.clearDateFilter(field)">清除</el-button>
            <el-button size="small" type="primary" @click="filters.applyDateFilter(field)">筛选</el-button>
          </div>
        </template>

        <!-- enum -->
        <template v-else-if="type === 'enum'">
          <el-checkbox-group
            :model-value="filters.enumValue(field)"
            @update:model-value="v => filters.setEnumValue(field, v as string[])"
          >
            <el-checkbox
              v-if="nullOption"
              :value="NULL_FILTER_VALUE"
              >{{ nullLabel || "（空）" }}</el-checkbox
            >
            <el-checkbox
              v-if="nullOption"
              :value="NOT_NULL_FILTER_VALUE"
              >（非空）</el-checkbox
            >
            <el-checkbox v-for="opt in normalizedOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="th-filter__btns">
            <el-button size="small" @click="filters.clearEnumFilter(field)">清除</el-button>
            <el-button size="small" type="primary" @click="filters.applyEnumFilter(field)">筛选</el-button>
          </div>
        </template>

        <!-- bool -->
        <template v-else-if="type === 'bool'">
          <el-radio-group
            :model-value="filters.boolValue(field)"
            @update:model-value="v => filters.setBoolValue(field, v as 'true' | 'false' | '')"
            @change="filters.applyBoolFilter(field)"
          >
            <el-radio value="">不限</el-radio>
            <el-radio value="true">是</el-radio>
            <el-radio value="false">否</el-radio>
          </el-radio-group>
          <div class="th-filter__btns">
            <el-button size="small" @click="filters.clearBoolFilter(field)">清除</el-button>
          </div>
        </template>

        <!-- 额外内容（如未关联企业入口），由调用方通过插槽注入；不传则不渲染 -->
        <slot name="extra" />
      </div>
    </el-popover>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { Filter } from "@element-plus/icons-vue";
import {
  NULL_FILTER_VALUE,
  NOT_NULL_FILTER_VALUE,
  type HeaderFilterHandlers
} from "@/composables/useHeaderFilters";

type FilterType = "text" | "number" | "date" | "enum" | "bool";

interface EnumOption {
  value: string;
  label: string;
}

const props = withDefaults(
  defineProps<{
    filters: HeaderFilterHandlers;
    field: string;
    label: string;
    type: FilterType;
    /** enum 选项：支持字符串数组或 {value,label} 数组 */
    options?: (string | EnumOption)[];
    placeholder?: string;
    /** enum 是否带"（空）"选项，默认带 */
    nullOption?: boolean;
    nullLabel?: string;
  }>(),
  {
    options: () => [],
    placeholder: "",
    nullOption: true,
    nullLabel: "（空）"
  }
);

const normalizedOptions = computed<EnumOption[]>(() =>
  props.options.map(o => (typeof o === "string" ? { value: o, label: o } : o))
);

const active = computed(() => {
  switch (props.type) {
    case "text":
      return props.filters.hasTextFilter(props.field);
    case "number":
      return props.filters.hasNumberFilter(props.field);
    case "date":
      return props.filters.hasDateFilter(props.field);
    case "enum":
      return props.filters.hasEnumFilter(props.field);
    case "bool":
      return props.filters.hasBoolFilter(props.field);
  }
});

const numRange = computed(() => props.filters.numberRange(props.field));
const dtRange = computed(() => props.filters.dateRange(props.field));
</script>

<style scoped>
.th-filter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.th-filter__icon {
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
}
.th-filter__icon.is-active {
  color: var(--el-color-primary);
}
.th-filter__panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.th-filter__nullness {
  display: flex;
  align-items: center;
  gap: 2px;
}
.th-filter__nullness .el-link {
  font-weight: 400;
}
.th-filter__nullness .el-link.is-active {
  font-weight: 600;
}
.th-filter__range {
  display: flex;
  align-items: center;
  gap: 4px;
}
.th-filter__num {
  width: 96px;
}
.th-filter__date {
  width: 100%;
}
.th-filter__sep {
  color: var(--el-text-color-placeholder);
}
.th-filter__btns {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
