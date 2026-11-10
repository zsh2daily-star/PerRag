<script setup lang="ts">
import {
  ref,
  reactive,
  computed,
  onMounted,
  onBeforeUnmount,
  watch
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  getMaterials,
  createMaterial,
  deleteMaterial,
  triggerIndex,
  rebuildIndex,
  getEvidence,
  updateScope
} from "@/api/materials";
import { fmtDate } from "@/utils/date";

defineOptions({ name: "Materials" });

type MaterialStatus = "pending" | "indexing" | "indexed" | "failed";
type ScopeMode = "A" | "B" | "C";

interface MaterialScope {
  scope_industry?: string;
  scope_region?: string;
  enterprise_codes?: string[];
}

interface Material {
  id: number;
  filename: string;
  format: string;
  status: MaterialStatus;
  pages: number;
  uploader: string;
  uploaded_at: string;
  scope: MaterialScope;
}

interface EvidenceChunk {
  id: number;
  page_number: number;
  text: string;
}

const loading = ref(false);
const tableData = ref<Material[]>([]);
const total = ref(0);

const query = reactive({
  keyword: "",
  status: "" as MaterialStatus | "",
  page: 1,
  page_size: 20
});

const statusOptions = [
  { label: "全部", value: "" },
  { label: "待处理", value: "pending" },
  { label: "索引中", value: "indexing" },
  { label: "已索引", value: "indexed" },
  { label: "失败", value: "failed" }
];

const statusTagType: Record<MaterialStatus, string> = {
  pending: "info",
  indexing: "",
  indexed: "success",
  failed: "danger"
};

const statusText: Record<MaterialStatus, string> = {
  pending: "待处理",
  indexing: "索引中",
  indexed: "已索引",
  failed: "失败"
};

const formatOptions = ["pdf", "docx", "xlsx", "pptx", "txt", "md"];
const industryOptions = ["化工", "制造", "食品", "建筑", "物流", "其他"];
const regionOptions = [
  "浦东新区",
  "徐汇区",
  "长宁区",
  "闵行区",
  "静安区",
  "其他"
];

// 上传弹窗
const uploadVisible = ref(false);
const uploadLoading = ref(false);
const uploadFormRef = ref<FormInstance>();
const uploadForm = reactive({
  filename: "",
  format: "pdf",
  scope_mode: "A" as ScopeMode,
  scope_industry: "",
  scope_region: "",
  enterprise_codes: [] as string[]
});

const uploadRules: FormRules = {
  filename: [{ required: true, message: "请输入文件名", trigger: "blur" }],
  format: [{ required: true, message: "请选择格式", trigger: "change" }]
};

// 范围更新弹窗
const scopeVisible = ref(false);
const scopeLoading = ref(false);
const scopeFormRef = ref<FormInstance>();
const scopeForm = reactive({
  id: 0,
  filename: "",
  scope_mode: "A" as ScopeMode,
  scope_industry: "",
  scope_region: "",
  enterprise_codes: [] as string[]
});

// 证据查看弹窗
const evidenceVisible = ref(false);
const evidenceLoading = ref(false);
const evidenceChunks = ref<EvidenceChunk[]>([]);
const evidenceIndex = ref(0);
const evidenceTitle = ref("");

// 状态轮询
let pollTimer: ReturnType<typeof setInterval> | null = null;

const hasIndexing = computed(() =>
  tableData.value.some(item => item.status === "indexing")
);

const startPolling = () => {
  stopPolling();
  pollTimer = setInterval(() => {
    if (hasIndexing.value) {
      fetchList(true);
    } else {
      stopPolling();
    }
  }, 3000);
};

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
};

watch(hasIndexing, val => {
  if (val) startPolling();
  else stopPolling();
});

const buildScopeTags = (scope: MaterialScope): string[] => {
  const tags: string[] = [];
  if (scope.scope_industry) tags.push(`行业:${scope.scope_industry}`);
  if (scope.scope_region) tags.push(`区域:${scope.scope_region}`);
  if (scope.enterprise_codes?.length)
    tags.push(`企业:${scope.enterprise_codes.length}家`);
  return tags.length ? tags : ["公共通用"];
};

const fetchList = async (silent = false) => {
  if (!silent) loading.value = true;
  try {
    const res = await getMaterials(query);
    tableData.value = res.list;
    total.value = res.total;
  } catch {
    if (!silent) ElMessage.error("加载材料列表失败");
  } finally {
    if (!silent) loading.value = false;
  }
};

const handleSearch = () => {
  query.page = 1;
  fetchList();
};

const handleReset = () => {
  query.keyword = "";
  query.status = "";
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

// 上传
const openUpload = () => {
  Object.assign(uploadForm, {
    filename: "",
    format: "pdf",
    scope_mode: "A",
    scope_industry: "",
    scope_region: "",
    enterprise_codes: []
  });
  uploadVisible.value = true;
};

const handleUploadSubmit = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    uploadLoading.value = true;
    try {
      const payload: any = {
        filename: uploadForm.filename,
        format: uploadForm.format,
        scope_mode: uploadForm.scope_mode
      };
      if (uploadForm.scope_mode === "B") {
        payload.scope_industry = uploadForm.scope_industry;
        payload.scope_region = uploadForm.scope_region;
      } else if (uploadForm.scope_mode === "C") {
        payload.enterprise_codes = uploadForm.enterprise_codes;
      }
      await createMaterial(payload);
      ElMessage.success("上传成功");
      uploadVisible.value = false;
      fetchList();
    } catch {
      ElMessage.error("上传失败");
    } finally {
      uploadLoading.value = false;
    }
  });
};

// 触发索引
const handleTriggerIndex = async (row: Material) => {
  try {
    await triggerIndex(row.id);
    ElMessage.success("已触发索引");
    fetchList();
  } catch {
    ElMessage.error("触发索引失败");
  }
};

// 重建索引
const handleRebuildIndex = async (row: Material) => {
  try {
    await rebuildIndex(row.id);
    ElMessage.success("已触发重建索引");
    fetchList();
  } catch {
    ElMessage.error("重建索引失败");
  }
};

// 更新范围
const openScope = (row: Material) => {
  Object.assign(scopeForm, {
    id: row.id,
    filename: row.filename,
    scope_mode: row.scope.enterprise_codes?.length
      ? "C"
      : row.scope.scope_industry || row.scope.scope_region
        ? "B"
        : "A",
    scope_industry: row.scope.scope_industry || "",
    scope_region: row.scope.scope_region || "",
    enterprise_codes: row.scope.enterprise_codes || []
  });
  scopeVisible.value = true;
};

const handleScopeSubmit = async () => {
  scopeLoading.value = true;
  try {
    const payload: any = { scope_mode: scopeForm.scope_mode };
    if (scopeForm.scope_mode === "B") {
      payload.scope_industry = scopeForm.scope_industry;
      payload.scope_region = scopeForm.scope_region;
    } else if (scopeForm.scope_mode === "C") {
      payload.enterprise_codes = scopeForm.enterprise_codes;
    }
    await updateScope(scopeForm.id, payload);
    ElMessage.success("范围更新成功");
    scopeVisible.value = false;
    fetchList();
  } catch {
    ElMessage.error("范围更新失败");
  } finally {
    scopeLoading.value = false;
  }
};

// 证据查看
const openEvidence = async (row: Material) => {
  evidenceVisible.value = true;
  evidenceLoading.value = true;
  evidenceTitle.value = row.filename;
  evidenceIndex.value = 0;
  evidenceChunks.value = [];
  try {
    const res = await getEvidence(row.id);
    evidenceChunks.value = res.chunks;
  } catch {
    ElMessage.error("加载证据失败");
  } finally {
    evidenceLoading.value = false;
  }
};

const evidencePrev = () => {
  if (evidenceIndex.value > 0) evidenceIndex.value--;
};

const evidenceNext = () => {
  if (evidenceIndex.value < evidenceChunks.value.length - 1)
    evidenceIndex.value++;
};

// 删除
const handleDelete = (row: Material) => {
  ElMessageBox.confirm(
    `确定要删除材料「${row.filename}」吗？`,
    "删除确认",
    { type: "warning", confirmButtonText: "确定", cancelButtonText: "取消" }
  )
    .then(async () => {
      try {
        await deleteMaterial(row.id);
        ElMessage.success("删除成功");
        fetchList();
      } catch {
        ElMessage.error("删除失败");
      }
    })
    .catch(() => {});
};

onMounted(() => {
  fetchList();
});

onBeforeUnmount(() => {
  stopPolling();
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
            placeholder="文件名/上传人"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="query.status"
            placeholder="全部"
            clearable
            style="width: 140px"
          >
            <el-option
              v-for="item in statusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
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
        <span class="font-bold">材料列表</span>
        <el-button type="primary" @click="openUpload">上传材料</el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="tableData"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column
          prop="filename"
          label="文件名"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column label="格式" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small">{{ row.format }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="范围标签" min-width="200">
          <template #default="{ row }">
            <el-tag
              v-for="(tag, i) in buildScopeTags(row.scope)"
              :key="i"
              size="small"
              type="info"
              class="mr-1"
            >
              {{ tag }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType[row.status]" size="small">
              {{ statusText[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="pages"
          label="页数"
          width="80"
          align="center"
        />
        <el-table-column prop="uploader" label="上传人" width="100" />
        <el-table-column
          prop="uploaded_at"
          label="上传时间"
          width="120"
          align="center"
        >
          <template #default="{ row }">{{ fmtDate(row.uploaded_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'pending' || row.status === 'failed'"
              link
              type="primary"
              size="small"
              @click="handleTriggerIndex(row)"
            >
              触发索引
            </el-button>
            <template v-if="row.status === 'indexed'">
              <el-button
                link
                type="primary"
                size="small"
                @click="handleRebuildIndex(row)"
              >
                重建索引
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                @click="openEvidence(row)"
              >
                查看证据
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                @click="openScope(row)"
              >
                更新范围
              </el-button>
            </template>
            <el-button
              link
              type="danger"
              size="small"
              @click="handleDelete(row)"
            >
              删除
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

    <!-- 上传弹窗 -->
    <el-dialog
      v-model="uploadVisible"
      title="上传材料"
      width="560px"
      destroy-on-close
    >
      <el-form
        ref="uploadFormRef"
        :model="uploadForm"
        :rules="uploadRules"
        label-width="100px"
      >
        <el-form-item label="文件名" prop="filename">
          <el-input v-model="uploadForm.filename" placeholder="请输入文件名" />
        </el-form-item>
        <el-form-item label="格式" prop="format">
          <el-select v-model="uploadForm.format" style="width: 100%">
            <el-option
              v-for="item in formatOptions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="范围类型">
          <el-radio-group v-model="uploadForm.scope_mode">
            <el-radio value="A">A 公共通用</el-radio>
            <el-radio value="B">B 类型定向</el-radio>
            <el-radio value="C">C 涉及企业</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="uploadForm.scope_mode === 'B'">
          <el-form-item label="适用行业">
            <el-select
              v-model="uploadForm.scope_industry"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option
                v-for="item in industryOptions"
                :key="item"
                :label="item"
                :value="item"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="适用区域">
            <el-select
              v-model="uploadForm.scope_region"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option
                v-for="item in regionOptions"
                :key="item"
                :label="item"
                :value="item"
              />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-if="uploadForm.scope_mode === 'C'" label="企业代码">
          <el-select
            v-model="uploadForm.enterprise_codes"
            multiple
            filterable
            allow-create
            placeholder="输入企业信用代码"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="uploadLoading"
          @click="handleUploadSubmit(uploadFormRef)"
        >
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 更新范围弹窗 -->
    <el-dialog
      v-model="scopeVisible"
      title="更新范围"
      width="560px"
      destroy-on-close
    >
      <div class="mb-4 text-sm text-gray-500">
        材料：{{ scopeForm.filename }}
      </div>
      <el-form label-width="100px">
        <el-form-item label="范围类型">
          <el-radio-group v-model="scopeForm.scope_mode">
            <el-radio value="A">A 公共通用</el-radio>
            <el-radio value="B">B 类型定向</el-radio>
            <el-radio value="C">C 涉及企业</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="scopeForm.scope_mode === 'B'">
          <el-form-item label="适用行业">
            <el-select
              v-model="scopeForm.scope_industry"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option
                v-for="item in industryOptions"
                :key="item"
                :label="item"
                :value="item"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="适用区域">
            <el-select
              v-model="scopeForm.scope_region"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option
                v-for="item in regionOptions"
                :key="item"
                :label="item"
                :value="item"
              />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-if="scopeForm.scope_mode === 'C'" label="企业代码">
          <el-select
            v-model="scopeForm.enterprise_codes"
            multiple
            filterable
            allow-create
            placeholder="输入企业信用代码"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="scopeVisible = false">取消</el-button>
        <el-button type="primary" :loading="scopeLoading" @click="handleScopeSubmit">
          确定
        </el-button>
      </template>
    </el-dialog>

    <!-- 证据查看弹窗 -->
    <el-dialog
      v-model="evidenceVisible"
      title="证据片段"
      width="640px"
      destroy-on-close
    >
      <div v-loading="evidenceLoading">
        <div class="mb-3 text-sm text-gray-500">
          {{ evidenceTitle }} · 共 {{ evidenceChunks.length }} 个片段
        </div>
        <el-card v-if="evidenceChunks.length" shadow="never">
          <div class="mb-2">
            <el-tag size="small" type="info">
              第 {{ evidenceChunks[evidenceIndex].page_number }} 页
            </el-tag>
            <span class="ml-2 text-xs text-gray-400">
              片段 {{ evidenceIndex + 1 }} / {{ evidenceChunks.length }}
            </span>
          </div>
          <div class="whitespace-pre-wrap text-sm leading-relaxed max-h-[300px] overflow-y-auto">
            {{ evidenceChunks[evidenceIndex].text }}
          </div>
        </el-card>
        <el-empty v-else description="暂无证据片段" />
      </div>

      <template #footer>
        <el-button
          :disabled="evidenceIndex === 0"
          @click="evidencePrev"
        >
          上一个
        </el-button>
        <el-button
          :disabled="evidenceIndex >= evidenceChunks.length - 1"
          type="primary"
          @click="evidenceNext"
        >
          下一个
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>
