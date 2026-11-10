<!--
  通用可编辑单元格 —— ReTablePage 在 col.editable 且无自定义 slot 时自动使用。

  交互：点击文本 → el-input 自动聚焦 → Enter 弹确认弹窗 → 确认后调 updateFn 保存
        Esc / 失焦 → 取消（确认弹窗打开时不会误取消）

  用法（页面一般不直接用，由 ReTablePage 自动渲染）：
    <EditableCell
      :value="row.description"
      label="隐患描述"
      :update-fn="async (v) => { await api(row.id, { field: v }); row.field = v; }"
    />
-->
<template>
  <el-input
    v-if="editing"
    :ref="focusInput"
    v-model="editValue"
    size="small"
    :disabled="saving"
    @keyup.enter="confirm"
    @keyup.escape="cancel"
    @blur="cancel"
  />
  <span v-else class="cursor-text" @click="start">{{ value || fallback }}</span>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

const props = withDefaults(
  defineProps<{
    /** 当前值 */
    value: string | null | undefined;
    /** 确认弹窗中的字段中文名 */
    label: string;
    /** 保存回调：接收新值，页面在此调 API + 更新行数据 */
    updateFn: (value: string) => Promise<void>;
    /** 空值时显示的占位符 */
    fallback?: string;
  }>(),
  { fallback: "-" }
);

const editing = ref(false);
const editValue = ref("");
const saving = ref(false);
const isConfirming = ref(false);

const start = () => {
  editing.value = true;
  editValue.value = props.value ?? "";
};

const cancel = () => {
  if (isConfirming.value) return;
  editing.value = false;
  editValue.value = "";
};

const confirm = async () => {
  if (isConfirming.value) return;
  if (editValue.value === (props.value ?? "")) {
    cancel();
    return;
  }
  isConfirming.value = true;
  try {
    await ElMessageBox.confirm(`确认修改${props.label}？`, "保存确认", {
      type: "warning",
      confirmButtonText: "保存",
      cancelButtonText: "取消"
    });
  } catch {
    isConfirming.value = false;
    cancel();
    return;
  }
  saving.value = true;
  try {
    await props.updateFn(editValue.value);
    ElMessage.success("已保存");
  } catch (e: any) {
    ElMessage.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
    isConfirming.value = false;
    editing.value = false;
    editValue.value = "";
  }
};

const focusInput = (el: any) => el?.focus?.();
</script>
