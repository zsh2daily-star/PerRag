<script setup lang="ts">
import { ref, reactive } from "vue";
import { ElMessage } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import { changePassword } from "@/api/user";
import { REGEXP_PWD } from "@/views/login/utils/rule";

defineOptions({ name: "ChangePasswordDialog" });

const visible = ref(false);
const loading = ref(false);
const formRef = ref<FormInstance>();

const form = reactive({
  old_password: "",
  new_password: "",
  confirm_password: ""
});

const rules: FormRules = {
  old_password: [
    { required: true, message: "请输入旧密码", trigger: "blur" }
  ],
  new_password: [
    { required: true, message: "请输入新密码", trigger: "blur" },
    {
      validator: (_rule, value: string, callback) => {
        if (!value) {
          callback(new Error("请输入新密码"));
        } else if (!REGEXP_PWD.test(value)) {
          callback(
            new Error("密码需 8-18 位，含数字/字母/符号至少两种组合")
          );
        } else if (value === form.old_password) {
          callback(new Error("新密码不能与旧密码相同"));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ],
  confirm_password: [
    { required: true, message: "请再次输入新密码", trigger: "blur" },
    {
      validator: (_rule, value: string, callback) => {
        if (value && value !== form.new_password) {
          callback(new Error("两次输入的密码不一致"));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
};

const resetForm = () => {
  form.old_password = "";
  form.new_password = "";
  form.confirm_password = "";
  formRef.value?.clearValidate();
};

const open = () => {
  resetForm();
  visible.value = true;
};

// 暴露给父组件通过 ref 调用（<script setup> 默认不暴露任何方法）
defineExpose({ open });

const handleClose = () => {
  resetForm();
  visible.value = false;
};

const handleSubmit = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    loading.value = true;
    try {
      await changePassword({
        old_password: form.old_password,
        new_password: form.new_password
      });
      ElMessage.success("密码修改成功");
      handleClose();
    } catch (err: any) {
      // 后端返回 400 时，response.data.detail 是错误信息
      const detail = err?.response?.data?.detail;
      ElMessage.error(detail || "密码修改失败");
    } finally {
      loading.value = false;
    }
  });
};
</script>

<template>
  <el-dialog
    v-model="visible"
    title="修改密码"
    width="440px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <el-alert
      type="info"
      :closable="false"
      title="如忘记密码，请联系管理员重置"
      class="mb-4"
    />
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="90px"
    >
      <el-form-item label="旧密码" prop="old_password">
        <el-input
          v-model="form.old_password"
          type="password"
          show-password
          placeholder="请输入当前密码"
        />
      </el-form-item>
      <el-form-item label="新密码" prop="new_password">
        <el-input
          v-model="form.new_password"
          type="password"
          show-password
          placeholder="8-18位，数字/字母/符号至少两种"
        />
      </el-form-item>
      <el-form-item label="确认密码" prop="confirm_password">
        <el-input
          v-model="form.confirm_password"
          type="password"
          show-password
          placeholder="请再次输入新密码"
          @keyup.enter="handleSubmit(formRef)"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button
        type="primary"
        :loading="loading"
        @click="handleSubmit(formRef)"
      >
        确定
      </el-button>
    </template>
  </el-dialog>
</template>
