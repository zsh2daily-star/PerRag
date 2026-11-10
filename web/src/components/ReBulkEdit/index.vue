<template>
  <el-dialog
    :model-value="modelValue"
    title="批量修改字段"
    width="420px"
    @update:model-value="v => emit('update:modelValue', v)"
  >
    <div class="space-y-3">
      <div class="text-sm text-gray-500">
        将对选中的 <b class="text-orange-500">{{ selectedCount }}</b> 条记录统一修改以下字段：
      </div>
      <el-form label-width="80px">
        <el-form-item label="修改字段">
          <el-select
            v-model="fieldKey"
            placeholder="请选择字段"
            style="width: 100%"
            @change="onFieldChange"
          >
            <el-option
              v-for="f in fields"
              :key="f.key"
              :label="f.label"
              :value="f.key"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="fieldKey" label="新值">
          <!-- 文本 -->
          <el-input
            v-if="currentField?.type === 'text'"
            v-model="value"
            placeholder="输入新值（留空即清空）"
            clearable
          />
          <!-- 数字 -->
          <el-input-number
            v-else-if="currentField?.type === 'number'"
            v-model="value"
            controls-position="right"
            style="width: 100%"
          />
          <!-- 布尔 -->
          <el-select
            v-else-if="currentField?.type === 'bool'"
            v-model="value"
            style="width: 100%"
          >
            <el-option label="是" :value="true" />
            <el-option label="否" :value="false" />
            <el-option label="未填报" :value="null" />
          </el-select>
          <!-- 枚举 -->
          <el-select
            v-else-if="currentField?.type === 'select'"
            v-model="value"
            style="width: 100%"
            clearable
          >
            <el-option
              v-for="opt in currentField?.options || []"
              :key="String(opt.value)"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <!-- 日期（精确到日） -->
          <el-date-picker
            v-else-if="currentField?.type === 'date'"
            v-model="value"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="loading"
        :disabled="!fieldKey"
        @click="onConfirm"
      >
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { ElMessage } from "element-plus";

/** 可批量修改的字段描述 */
export interface BulkEditField {
  /** 记录字段名（与后端更新接口字段一致） */
  key: string;
  /** 显示标签 */
  label: string;
  type: "text" | "number" | "bool" | "select" | "date";
  /** type=select 时的可选项 */
  options?: { value: any; label: string }[];
}

const props = defineProps<{
  modelValue: boolean;
  fields: BulkEditField[];
  selectedCount: number;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "confirm", payload: { field: string; value: any }): void;
}>();

const fieldKey = ref("");
const value = ref<any>("");

const currentField = computed(() =>
  props.fields.find(f => f.key === fieldKey.value)
);

/** 弹窗打开时重置 */
watch(
  () => props.modelValue,
  v => {
    if (v) {
      fieldKey.value = "";
      value.value = "";
    }
  }
);

const onFieldChange = () => {
  const f = currentField.value;
  if (!f) {
    value.value = "";
    return;
  }
  if (f.type === "bool") value.value = null;
  else if (f.type === "number") value.value = 0;
  else if (f.type === "date") value.value = "";
  else value.value = "";
};

const onConfirm = () => {
  const f = currentField.value;
  if (!f) {
    ElMessage.warning("请选择要修改的字段");
    return;
  }
  emit("confirm", { field: f.key, value: value.value });
};
</script>
