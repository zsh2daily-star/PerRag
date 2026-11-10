<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { ElMessage } from "element-plus";
import {
  getAuditLogs,
  getAuditActions,
  getAuditStaffOptions
} from "@/api/audit";

defineOptions({ name: "SystemAudit" });

interface AuditLog {
  id: number;
  created_at: string;
  operator: string;
  action: string;
  resource_type: string;
  resource_id: string;
  status: "success" | "failed";
  duration_ms: number;
  ip: string;
  error_message?: string;
}

interface OptionItem {
  label: string;
  value: string;
}

const loading = ref(false);
const tableData = ref<AuditLog[]>([]);
const total = ref(0);
const actionOptions = ref<OptionItem[]>([]);
const staffOptions = ref<OptionItem[]>([]);

const dateRange = ref<[string, string] | null>(null);

const query = reactive({
  action: "",
  staff: "",
  start_date: "",
  end_date: "",
  page: 1,
  page_size: 20
});

const statusTagType: Record<string, string> = {
  success: "success",
  failed: "danger"
};

const statusText: Record<string, string> = {
  success: "成功",
  failed: "失败"
};

/** 业务语义动作 → 中文（业务层显式记录的动作） */
const ACTION_LABELS: Record<string, string> = {
  "auth.login": "登录",
  "auth.logout": "退出登录",
  "auth.change_password": "修改密码",
  "admin.staff.create": "新建账号",
  "admin.staff.reset_password": "重置账号密码",
  "admin.staff.activate": "启用账号",
  "admin.staff.deactivate": "停用账号",
  "enterprise.create": "新建企业",
  "enterprise.update": "编辑企业",
  "enterprise.delete": "删除企业",
  "material.upload": "上传材料",
  "material.index": "材料建立索引",
  "material.reindex": "材料重建索引",
  "material.scope_update": "修改材料适用范围",
  "material.delete": "删除材料",
  "inspection.create": "新建巡查记录",
  "inspection.delete": "删除巡查记录",
  "inspection.status.rectifying": "隐患转整改中",
  "inspection.status.closed": "隐患闭环"
};

/** 中间件兜底动作 http.{method}.{path} 中的 HTTP 方法 → 中文 */
const METHOD_LABELS: Record<string, string> = {
  get: "查看",
  post: "提交",
  put: "更新",
  patch: "更新",
  delete: "删除"
};

/** 接口路径前缀 → 业务模块中文名 */
const PATH_MODULES: { pattern: RegExp; label: string }[] = [
  { pattern: /^\/enterprises/, label: "企业" },
  { pattern: /^\/materials/, label: "材料" },
  { pattern: /^\/inspection-records/, label: "巡查记录" },
  { pattern: /^\/admin\/staff/, label: "账号" },
  { pattern: /^\/admin\/audit-logs/, label: "审计日志" },
  { pattern: /^\/auth\/login/, label: "登录" },
  { pattern: /^\/auth\/change-password/, label: "修改密码" },
  { pattern: /^\/auth\/me/, label: "登录信息" },
  { pattern: /^\/dashboard/, label: "工作台" },
  { pattern: /^\/evidence/, label: "佐证材料" },
  { pattern: /^\/index/, label: "索引任务" }
];

/** 路径末尾的动作段 → 中文动词（优先于 HTTP 方法直译） */
const PATH_SUFFIXES: { pattern: RegExp; label: string }[] = [
  { pattern: /\/deactivate$/, label: "停用" },
  { pattern: /\/activate$/, label: "启用" },
  { pattern: /\/reset-password$/, label: "重置密码" },
  { pattern: /\/change-password$/, label: "修改密码" },
  { pattern: /\/scope$/, label: "设置范围" },
  { pattern: /\/reindex$/, label: "重建索引" },
  { pattern: /\/index$/, label: "建立索引" }
];

/** 资源类型 → 中文 */
const RESOURCE_LABELS: Record<string, string> = {
  enterprise: "企业",
  material: "材料",
  inspection: "巡查记录",
  inspection_record: "巡查记录",
  staff: "账号"
};

/** 把原始 action 翻译为业务人员可读的中文描述 */
const actionLabel = (action: string): string => {
  if (!action) return "—";
  // 1. 固定业务语义动作
  if (ACTION_LABELS[action]) return ACTION_LABELS[action];
  // 2. 中间件兜底动作：http.{method}.{path}
  if (action.startsWith("http.")) {
    const rest = action.slice("http.".length);
    const dot = rest.indexOf(".");
    const method = dot >= 0 ? rest.slice(0, dot) : rest;
    let path = dot >= 0 ? rest.slice(dot + 1) : "";
    path = path.replace(/^\/api/, ""); // 兼容历史 /api 前缀
    const methodText = METHOD_LABELS[method] || method.toUpperCase();
    const mod = PATH_MODULES.find(m => m.pattern.test(path));
    if (mod) {
      // 模块名本身即动作语义（登录/修改密码）
      if (mod.label === "登录" || mod.label === "修改密码") return mod.label;
      // 末段为资源 ID（数字/UUID）时展示为「模块 #ID」
      const idMatch = path.match(/\/([0-9]+|[0-9a-f-]{36})(?:\/|$)/);
      const suffix = PATH_SUFFIXES.find(s => s.pattern.test(path));
      // 1) 路径带动作后缀：账号 #5 停用 / 材料 #1000 设置范围
      if (suffix) {
        return idMatch
          ? `${mod.label} #${idMatch[1]} ${suffix.label}`
          : `${suffix.label}${mod.label}`;
      }
      // 2) POST 集合路径 = 新建资源
      if (method === "post" && !idMatch) return `新建${mod.label}`;
      // 3) 常规：方法 + 模块（#ID）
      return idMatch
        ? `${methodText}${mod.label} #${idMatch[1]}`
        : `${methodText}${mod.label}`;
    }
    return `接口 ${method.toUpperCase()} ${path || ""}`.trim();
  }
  return action;
};

const resourceLabel = (resourceType: string | null): string =>
  (resourceType && RESOURCE_LABELS[resourceType]) || resourceType || "—";

const fetchOptions = async () => {
  try {
    const [actionRes, staffRes] = await Promise.all([
      getAuditActions(),
      getAuditStaffOptions()
    ]);
    actionOptions.value = actionRes;
    staffOptions.value = staffRes;
  } catch {
    // 选项加载失败不阻塞主流程
  }
};

const fetchList = async () => {
  loading.value = true;
  if (dateRange.value && dateRange.value.length === 2) {
    query.start_date = dateRange.value[0];
    query.end_date = dateRange.value[1];
  } else {
    query.start_date = "";
    query.end_date = "";
  }
  try {
    const res = await getAuditLogs(query);
    tableData.value = res.list;
    total.value = res.total;
  } catch {
    ElMessage.error("加载审计日志失败");
  } finally {
    loading.value = false;
  }
};

const handleSearch = () => {
  query.page = 1;
  fetchList();
};

const handleReset = () => {
  query.action = "";
  query.staff = "";
  dateRange.value = null;
  query.start_date = "";
  query.end_date = "";
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

onMounted(() => {
  fetchOptions();
  fetchList();
});
</script>

<template>
  <div class="p-4">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="mb-4">
      <el-form inline @submit.prevent>
        <el-form-item label="动作">
          <el-select
            v-model="query.action"
            placeholder="全部动作"
            clearable
            style="width: 160px"
          >
            <el-option
              v-for="item in actionOptions"
              :key="item.value"
              :label="actionLabel(item.value)"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="操作人">
          <el-select
            v-model="query.staff"
            placeholder="全部操作人"
            clearable
            filterable
            style="width: 160px"
          >
            <el-option
              v-for="item in staffOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 260px"
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
        <span class="font-bold">审计日志</span>
        <el-tag type="info" size="small">只读</el-tag>
      </div>

      <el-table
        v-loading="loading"
        :data="tableData"
        border
        stripe
        style="width: 100%"
        row-key="id"
      >
        <el-table-column
          prop="created_at"
          label="时间"
          width="170"
          align="center"
        />
        <el-table-column prop="operator" label="操作人" width="110" />
        <el-table-column prop="action" label="动作" width="170">
          <template #default="{ row }">
            <span :title="row.action">{{ actionLabel(row.action) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="resource_type"
          label="资源类型"
          width="120"
          align="center"
        >
          <template #default="{ row }">
            <el-tag v-if="row.resource_type" size="small" type="info">{{
              resourceLabel(row.resource_type)
            }}</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="resource_id"
          label="资源ID"
          width="120"
          align="center"
          show-overflow-tooltip
        />
        <el-table-column label="状态" width="180" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.status === 'failed' && row.error_message"
              :content="row.error_message"
              placement="top"
            >
              <el-tag :type="statusTagType[row.status]" size="small">
                {{ statusText[row.status] }}
              </el-tag>
            </el-tooltip>
            <el-tag
              v-else
              :type="statusTagType[row.status]"
              size="small"
            >
              {{ statusText[row.status] }}
            </el-tag>
            <div
              v-if="row.status === 'failed' && row.error_message"
              class="text-xs text-red-500 mt-1 leading-tight break-all"
            >
              {{ row.error_message }}
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="耗时(ms)"
          width="100"
          align="center"
        >
          <template #default="{ row }">
            {{ row.duration_ms }}
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="140" align="center" />
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
  </div>
</template>
