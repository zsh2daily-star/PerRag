<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import {
  getStaff,
  createStaff,
  updateStaff,
  resetPassword,
  toggleStatus,
  updateStaffChatSettings
} from "@/api/staff";
import { fmtDate } from "@/utils/date";

defineOptions({ name: "SystemStaff" });

interface Staff {
  id: number;
  username: string;
  name: string;
  department: string;
  position: string;
  phone: string;
  is_admin: boolean;
  is_active: boolean;
  last_login: string | null;
  chat_daily_limit_yuan: number | null;
  chat_locked: boolean;
}

const userStore = useUserStoreHook();
const currentUsername = computed(() => userStore.username);

const loading = ref(false);
const tableData = ref<Staff[]>([]);
const total = ref(0);

const query = reactive({
  keyword: "",
  page: 1,
  page_size: 20
});

// 新建/编辑弹窗
const dialogVisible = ref(false);
const dialogTitle = ref("新建账号");
const submitLoading = ref(false);
const formRef = ref<FormInstance>();
const isEdit = ref(false);

const form = reactive({
  id: 0,
  username: "",
  name: "",
  department: "",
  position: "",
  phone: "",
  is_admin: false
});

const rules: FormRules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  name: [{ required: true, message: "请输入姓名", trigger: "blur" }]
};

// 初始密码弹窗
const passwordVisible = ref(false);
const initialPassword = ref("");

const fetchList = async () => {
  loading.value = true;
  try {
    const res = await getStaff(query);
    tableData.value = res.list;
    total.value = res.total;
  } catch {
    ElMessage.error("加载账号列表失败");
  } finally {
    loading.value = false;
  }
};

const handleSearch = () => {
  query.page = 1;
  fetchList();
};

const handleReset = () => {
  query.keyword = "";
  query.page = 1;
  fetchList();
};

const handlePageChange = (page: number) => {
  query.page = page;
  fetchList();
};

const handleSizeChange = (size: number) => {
  query.page_size = size;
  query.page = 1;
  fetchList();
};

const resetForm = () => {
  Object.assign(form, {
    id: 0,
    username: "",
    name: "",
    department: "",
    position: "",
    phone: "",
    is_admin: false
  });
};

const openCreate = () => {
  isEdit.value = false;
  dialogTitle.value = "新建账号";
  resetForm();
  dialogVisible.value = true;
};

const openEdit = (row: Staff) => {
  isEdit.value = true;
  dialogTitle.value = "编辑账号";
  Object.assign(form, {
    id: row.id,
    username: row.username,
    name: row.name,
    department: row.department,
    position: row.position,
    phone: row.phone,
    is_admin: row.is_admin
  });
  dialogVisible.value = true;
};

const handleSubmit = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    submitLoading.value = true;
    try {
      if (isEdit.value) {
        await updateStaff(form);
        ElMessage.success("更新成功");
        dialogVisible.value = false;
        fetchList();
      } else {
        const res = await createStaff(form);
        ElMessage.success("创建成功");
        dialogVisible.value = false;
        initialPassword.value = res.password;
        passwordVisible.value = true;
        fetchList();
      }
    } catch {
      ElMessage.error(isEdit.value ? "更新失败" : "创建失败");
    } finally {
      submitLoading.value = false;
    }
  });
};

const handleCopyPassword = async () => {
  try {
    await navigator.clipboard.writeText(initialPassword.value);
    ElMessage.success("已复制到剪贴板");
  } catch {
    ElMessage.warning("复制失败，请手动复制");
  }
};

// 重置密码
const handleResetPassword = (row: Staff) => {
  ElMessageBox.confirm(
    `确定要重置「${row.name}」的密码吗？`,
    "重置密码",
    { type: "warning", confirmButtonText: "确定", cancelButtonText: "取消" }
  )
    .then(async () => {
      try {
        const res = await resetPassword(row.id);
        initialPassword.value = res.password;
        passwordVisible.value = true;
        ElMessage.success("密码已重置");
      } catch {
        ElMessage.error("重置失败");
      }
    })
    .catch(() => {});
};

// 停用/启用
const isSelf = (row: Staff) => row.username === currentUsername.value;

const activeAdminCount = computed(
  () =>
    tableData.value.filter(s => s.is_admin && s.is_active).length
);

const handleToggleStatus = (row: Staff) => {
  if (row.is_active && isSelf(row)) {
    ElMessage.warning("不能停用当前登录账号");
    return;
  }
  if (
    row.is_active &&
    row.is_admin &&
    activeAdminCount.value <= 1
  ) {
    ElMessage.warning("不能停用最后一个启用的管理员");
    return;
  }

  const action = row.is_active ? "停用" : "启用";
  ElMessageBox.confirm(
    `确定要${action}账号「${row.name}」吗？`,
    `${action}确认`,
    { type: "warning", confirmButtonText: "确定", cancelButtonText: "取消" }
  )
    .then(async () => {
      try {
        await toggleStatus(row.id);
        ElMessage.success(`${action}成功`);
        fetchList();
      } catch {
        ElMessage.error(`${action}失败`);
      }
    })
    .catch(() => {});
};

// ── 对话配额管理 ──
const chatDialogVisible = ref(false);
const chatSubmitLoading = ref(false);
const chatForm = reactive({
  id: 0,
  name: "",
  is_admin: false,
  chat_daily_limit_yuan: 1.0 as number | null,
  chat_locked: false
});

const openChatDialog = (row: Staff) => {
  chatForm.id = row.id;
  chatForm.name = row.name;
  chatForm.is_admin = row.is_admin;
  chatForm.chat_daily_limit_yuan = row.chat_daily_limit_yuan;
  chatForm.chat_locked = row.chat_locked;
  chatDialogVisible.value = true;
};

const handleChatSubmit = async () => {
  chatSubmitLoading.value = true;
  try {
    await updateStaffChatSettings(chatForm.id, {
      chat_daily_limit_yuan: chatForm.chat_daily_limit_yuan,
      chat_locked: chatForm.chat_locked
    });
    ElMessage.success("已更新");
    chatDialogVisible.value = false;
    fetchList();
  } catch {
    ElMessage.error("更新失败");
  } finally {
    chatSubmitLoading.value = false;
  }
};

const handleToggleChatLock = (row: Staff) => {
  const action = row.chat_locked ? "解锁" : "锁定";
  const extra = row.chat_locked
    ? ""
    : "锁定后该用户将无法使用对话功能";
  ElMessageBox.confirm(
    `确定要${action}「${row.name}」的对话功能吗？${extra}`,
    `${action}对话`,
    { type: "warning", confirmButtonText: "确定", cancelButtonText: "取消" }
  )
    .then(async () => {
      try {
        await updateStaffChatSettings(row.id, {
          chat_locked: !row.chat_locked
        });
        ElMessage.success(`${action}成功`);
        fetchList();
      } catch {
        ElMessage.error(`${action}失败`);
      }
    })
    .catch(() => {});
};

onMounted(() => {
  fetchList();
});
</script>

<template>
  <div class="p-4">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="mb-4">
      <el-form inline @submit.prevent>
        <el-form-item label="关键词">
          <el-input
            v-model="query.keyword"
            placeholder="用户名/姓名/手机"
            clearable
            style="width: 220px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 表格 -->
    <el-card shadow="never">
      <div class="flex justify-between items-center mb-4">
        <span class="font-bold">账号列表</span>
        <el-button type="primary" @click="openCreate">新建账号</el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="tableData"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="department" label="部门" width="120" />
        <el-table-column prop="position" label="岗位" width="120" />
        <el-table-column prop="phone" label="手机" width="130" />
        <el-table-column label="管理员" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" type="danger" size="small">
              管理员
            </el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? "启用" : "停用" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="对话限额" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.is_admin" class="text-gray-400">不受限</span>
            <span v-else-if="row.chat_daily_limit_yuan === null">默认 ¥1.00</span>
            <span v-else>¥{{ row.chat_daily_limit_yuan.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="对话状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.chat_locked" type="danger" size="small">已锁定</el-tag>
            <el-tag v-else-if="row.is_admin" type="success" size="small">正常</el-tag>
            <el-tag v-else type="info" size="small">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后登录" width="130" align="center">
          <template #default="{ row }">
            {{ fmtDate(row.last_login) || "未登录" }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="340" fixed="right" align="center">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click="openEdit(row)"
            >
              编辑
            </el-button>
            <el-button
              link
              type="warning"
              size="small"
              @click="handleResetPassword(row)"
            >
              重置密码
            </el-button>
            <el-button
              link
              type="info"
              size="small"
              @click="openChatDialog(row)"
            >
              对话配置
            </el-button>
            <el-button
              link
              :type="row.chat_locked ? 'success' : 'danger'"
              size="small"
              @click="handleToggleChatLock(row)"
            >
              {{ row.chat_locked ? "解锁对话" : "锁定对话" }}
            </el-button>
            <el-button
              link
              :type="row.is_active ? 'danger' : 'success'"
              size="small"
              @click="handleToggleStatus(row)"
            >
              {{ row.is_active ? "停用" : "启用" }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end mt-4">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <!-- 新建/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="520px"
      destroy-on-close
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="80px"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            :disabled="isEdit"
          />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="请输入姓名" />
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="form.department" placeholder="请输入部门" />
        </el-form-item>
        <el-form-item label="岗位">
          <el-input v-model="form.position" placeholder="请输入岗位" />
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="form.phone" placeholder="请输入手机号" />
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch v-model="form.is_admin" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="submitLoading"
          @click="handleSubmit(formRef)"
        >
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 初始密码弹窗 -->
    <el-dialog
      v-model="passwordVisible"
      title="初始密码"
      width="420px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-alert
        type="warning"
        :closable="false"
        title="请妥善保存此密码，关闭后将无法再次查看"
        class="mb-4"
      />
      <div class="flex items-center gap-2">
        <el-input
          :model-value="initialPassword"
          readonly
          class="flex-1"
        />
        <el-button type="primary" @click="handleCopyPassword">
          复制
        </el-button>
      </div>

      <template #footer>
        <el-button type="primary" @click="passwordVisible = false">
          我已保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 对话配置弹窗 -->
    <el-dialog
      v-model="chatDialogVisible"
      :title="`对话配置 — ${chatForm.name}`"
      width="480px"
      destroy-on-close
    >
      <el-form label-width="110px">
        <el-form-item label="管理员">
          <el-tag v-if="chatForm.is_admin" type="danger" size="small">管理员不受限</el-tag>
          <span v-else class="text-gray-400">普通用户</span>
        </el-form-item>
        <el-form-item label="每日限额">
          <el-input-number
            v-if="!chatForm.is_admin"
            v-model="chatForm.chat_daily_limit_yuan"
            :min="0"
            :precision="2"
            :step="0.5"
            controls-position="right"
            style="width: 180px"
          />
          <div v-else class="text-gray-400">管理员不受限额</div>
          <div v-if="!chatForm.is_admin" class="text-xs text-gray-400 mt-1">
            留空（null）使用系统默认 ¥1.00；设为 0 即等同于永久超限
          </div>
        </el-form-item>
        <el-form-item label="锁定对话">
          <el-switch
            v-model="chatForm.chat_locked"
            :active-text="chatForm.chat_locked ? '已锁定' : '未锁定'"
          />
          <div class="text-xs text-gray-400 mt-1">
            锁定后该用户将无法使用对话功能，管理员手动解锁后恢复
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="chatDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="chatSubmitLoading"
          @click="handleChatSubmit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>
