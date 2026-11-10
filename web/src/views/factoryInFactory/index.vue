<script setup lang="ts">
import { reactive, ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  createFactoryInFactory,
  updateFactoryInFactory,
  getFactoryInFactoryBatches
} from "@/api/factoryInFactory";
import type { FactoryInFactory } from "@/api/factoryInFactory";
import { getEnterpriseList } from "@/api/enterprises";
import ReTablePage from "@/components/ReTablePage/index.vue";
import ReContactCell from "@/components/ReContactCell/index.vue";
import type {
  TablePageConfig,
  TablePageExpose
} from "@/components/ReTablePage/types";
import { buildFactoryConfig } from "./table.config";

defineOptions({ name: "FactoryInFactory" });

const tableRef = ref<TablePageExpose<FactoryInFactory>>();

// ── 合并单元格：同一出租方的多行，出租方相关列纵向合并 ──
/** 需要按出租方合并的列 prop 集合（无 prop 的组合列用 key） */
const LESSOR_MERGE_PROPS = new Set([
  "lessor_name",
  "lessor_contact",
  "lessor_use_area"
]);
/** 出租方分组键：优先用外键 ID，回退名称 */
const lessorKey = (r: FactoryInFactory): string =>
  r.lessor_enterprise_id != null
    ? `id:${r.lessor_enterprise_id}`
    : `name:${r.lessor_name ?? ""}`;
/** 每行的合并信息：rowIndex → { rowspan, isStart } */
const mergeInfo = ref<Record<number, { rowspan: number; isStart: boolean }>>(
  {}
);

/** 按出租方分组稳定排序，保证同一出租方的多行连续（组内保持后端原顺序） */
const sortByLessor = (rows: FactoryInFactory[]): FactoryInFactory[] => {
  const stable = rows.map((r, i) => ({ r, i }));
  stable.sort((a, b) => {
    const ka = lessorKey(a.r);
    const kb = lessorKey(b.r);
    return ka !== kb ? ka.localeCompare(kb) : a.i - b.i;
  });
  return stable.map(x => x.r);
};

/** 依据当前数据预计算出租方分组的合并信息 */
const computeMergeInfo = (rows: FactoryInFactory[]) => {
  const info: Record<number, { rowspan: number; isStart: boolean }> = {};
  let i = 0;
  while (i < rows.length) {
    const cur = lessorKey(rows[i]);
    let count = 1;
    while (i + count < rows.length && lessorKey(rows[i + count]) === cur) {
      count++;
    }
    info[i] = { rowspan: count, isStart: true };
    for (let j = 1; j < count; j++) {
      info[i + j] = { rowspan: 0, isStart: false };
    }
    i += count;
  }
  mergeInfo.value = info;
};

/** el-table span-method：出租方列按分组合并（通过 column.property 定位，列显隐后不错位） */
const spanMethod = ({
  column,
  rowIndex
}: {
  row: FactoryInFactory;
  column: any;
  rowIndex: number;
  columnIndex: number;
}) => {
  if (LESSOR_MERGE_PROPS.has(column?.property)) {
    const info = mergeInfo.value[rowIndex];
    if (!info) return { rowspan: 1, colspan: 1 };
    return info.isStart
      ? { rowspan: info.rowspan, colspan: 1 }
      : { rowspan: 0, colspan: 0 };
  }
};

// ── 表格配置（列/筛选/批量/导入导出全部声明式）──────────
const config: TablePageConfig<FactoryInFactory> = buildFactoryConfig({
  spanMethod,
  transformRows: sortByLessor,
  onRowsLoaded: computeMergeInfo
});

// ── 检查批次下拉（表单默认值用）─────────────────────────
const batchOptions = ref<string[]>([]);
const fetchBatchOptions = async () => {
  try {
    batchOptions.value = (await getFactoryInFactoryBatches()) ?? [];
  } catch {
    batchOptions.value = [];
  }
};

// ── 企业下拉选项（出租方/承租方选择器用；数据源 = Enterprise 主表）──
const entOptions = ref<{ id: number; name: string }[]>([]);
const fetchEntOptions = async () => {
  try {
    const { list } = await getEnterpriseList({ page: 1, page_size: 1000 });
    entOptions.value = (list ?? []).map(e => ({ id: e.id, name: e.name }));
  } catch {
    entOptions.value = [];
  }
};

// ── 新建/编辑弹窗 ───────────────────────────────────────
const dialogVisible = ref(false);
const dialogTitle = ref("新建记录");
const submitLoading = ref(false);
const formRef = ref<FormInstance>();
const isEdit = ref(false);

interface FactoryForm {
  id: number;
  inspection_batch: string;
  lessor_enterprise_id: number | null;
  tenant_enterprise_id: number | null;
  region: string;
  address: string;
  lessor_use_area: string;
  tenant_area: number | null;
  agreement_signed: boolean | null;
  site_category: string;
  inspection_date: string;
}

const emptyForm = (): FactoryForm => ({
  id: 0,
  inspection_batch: "",
  lessor_enterprise_id: null,
  tenant_enterprise_id: null,
  region: "",
  address: "",
  lessor_use_area: "",
  tenant_area: null,
  agreement_signed: null,
  site_category: "",
  inspection_date: ""
});

const form = reactive<FactoryForm>(emptyForm());

const rules: FormRules = {
  inspection_batch: [
    { required: true, message: "请输入检查批次", trigger: "blur" }
  ],
  lessor_enterprise_id: [
    { required: true, message: "请选择出租方企业", trigger: "change" }
  ],
  tenant_enterprise_id: [
    { required: true, message: "请选择承租方企业", trigger: "change" }
  ]
};

const openCreate = () => {
  isEdit.value = false;
  dialogTitle.value = "新建记录";
  Object.assign(form, emptyForm());
  // 默认填入首个批次（如果有）
  if (batchOptions.value.length && !form.inspection_batch) {
    form.inspection_batch = batchOptions.value[0];
  }
  dialogVisible.value = true;
};

const openEdit = (row: FactoryInFactory) => {
  isEdit.value = true;
  dialogTitle.value = "编辑记录";
  Object.assign(form, emptyForm(), {
    id: row.id,
    inspection_batch: row.inspection_batch ?? "",
    lessor_enterprise_id: row.lessor_enterprise_id,
    tenant_enterprise_id: row.tenant_enterprise_id,
    region: row.region ?? "",
    address: row.address ?? "",
    lessor_use_area: row.lessor_use_area ?? "",
    tenant_area: row.tenant_area ?? null,
    agreement_signed: row.agreement_signed ?? null,
    site_category: row.site_category ?? "",
    inspection_date: row.inspection_date ?? ""
  });
  dialogVisible.value = true;
};

const handleSubmit = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    submitLoading.value = true;
    const { id, ...payload } = form;
    try {
      if (isEdit.value) {
        await updateFactoryInFactory(form.id, payload);
        ElMessage.success("更新成功");
      } else {
        await createFactoryInFactory(payload);
        ElMessage.success("新建成功");
        fetchBatchOptions();
      }
      dialogVisible.value = false;
      tableRef.value?.reload();
    } catch {
      ElMessage.error(isEdit.value ? "更新失败" : "新建失败");
    } finally {
      submitLoading.value = false;
    }
  });
};

onMounted(() => {
  fetchBatchOptions();
  fetchEntOptions();
});
</script>

<template>
  <div>
    <ReTablePage ref="tableRef" :config="config">
    <!-- 工具栏：新建记录 -->
    <template #toolbar-extra>
      <el-button type="primary" @click="openCreate">新建记录</el-button>
    </template>

    <!-- 自定义单元格（企业名来自 Enterprise 主表，只读展示） -->
    <template #cell-lessor_name="{ row }: { row: FactoryInFactory }">
      <span :class="{ 'is-unlinked': !row.lessor_enterprise_id }">
        {{ row.lessor_name || "—" }}
      </span>
    </template>

    <template #cell-tenant_name="{ row }: { row: FactoryInFactory }">
      <span :class="{ 'is-unlinked': !row.tenant_enterprise_id }">
        {{ row.tenant_name || "—" }}
      </span>
    </template>

    <template #cell-lessor_contact="{ row }: { row: FactoryInFactory }">
      <ReContactCell
        :person="row.lessor_contact_person"
        :phone="row.lessor_contact_phone"
      />
    </template>

    <template #cell-tenant_contact="{ row }: { row: FactoryInFactory }">
      <ReContactCell
        :person="row.tenant_contact_person"
        :phone="row.tenant_contact_phone"
      />
    </template>

    <template #cell-agreement_signed="{ row }: { row: FactoryInFactory }">
      <el-tag v-if="row.agreement_signed === true" size="small" type="success">
        已签
      </el-tag>
      <el-tag
        v-else-if="row.agreement_signed === false"
        size="small"
        type="danger"
      >
        未签
      </el-tag>
      <span v-else>—</span>
    </template>

    <!-- 操作列：编辑 + 删除（删除按钮由基座默认渲染） -->
    <template #actions-prepend="{ row }: { row: FactoryInFactory }">
      <el-button link type="primary" size="small" @click="openEdit(row)">
        编辑
      </el-button>
    </template>

    <!-- 导入结果（本页格式：raw_records + total_counts + 未匹配企业 + 提交失败明细） -->
    <template #import-result="{ result }">
      <template v-if="result.total_counts">
        <el-alert type="success" :closable="false" class="mb-4">
          共解析 {{ result.raw_records || 0 }} 条，新增
          {{ result.total_counts?.created || 0 }} 条，更新
          {{ result.total_counts?.updated || 0 }} 条，跳过
          {{ result.total_counts?.skipped || 0 }} 条，失败
          {{ result.total_counts?.failed || 0 }} 条
        </el-alert>

        <div v-if="result.unmatched_enterprises?.length" class="mb-4">
          <div class="mb-2 text-sm font-bold text-orange-600">
            未匹配企业（{{ result.unmatched_enterprises.length }}
            家，记录已保留但未关联企业档案）：
          </div>
          <div class="flex flex-wrap gap-1">
            <el-tag
              v-for="name in result.unmatched_enterprises"
              :key="name"
              type="warning"
              effect="plain"
            >
              {{ name }}
            </el-tag>
          </div>
        </div>

        <el-alert
          v-if="result.commit_failures?.length"
          type="error"
          :closable="false"
          class="mb-2"
        >
          <div class="text-sm font-bold mb-1">提交失败明细（前 10 条）：</div>
          <div
            v-for="(f, i) in result.commit_failures"
            :key="i"
            class="text-xs"
          >
            {{ f.tenant || "—" }}：{{ f.error }}
          </div>
        </el-alert>
      </template>
      <el-alert
        v-else
        type="error"
        :title="result.message || '导入失败'"
        :closable="false"
      />
    </template>
  </ReTablePage>

  <!-- 新建/编辑弹窗 -->
  <el-dialog
    v-model="dialogVisible"
    :title="dialogTitle"
    width="780px"
    :close-on-click-modal="false"
  >
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="120px"
      label-position="right"
    >
      <el-divider content-position="left">批次信息</el-divider>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="检查批次" prop="inspection_batch">
            <el-input
              v-model="form.inspection_batch"
              placeholder="如 2026-厂中厂消防"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="排查日期">
            <el-date-picker
              v-model="form.inspection_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择日期"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="县市区">
            <el-input v-model="form.region" placeholder="如 开发区" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="具体地址">
            <el-input v-model="form.address" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="场所类别">
            <el-input v-model="form.site_category" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-divider content-position="left">出租方</el-divider>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="出租方企业" prop="lessor_enterprise_id">
            <el-select
              v-model="form.lessor_enterprise_id"
              placeholder="从企业台账选择"
              filterable
              style="width: 100%"
            >
              <el-option
                v-for="e in entOptions"
                :key="e.id"
                :label="e.name"
                :value="e.id"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="出租方使用面积">
            <el-input v-model="form.lessor_use_area" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-divider content-position="left">承租方</el-divider>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="承租方企业" prop="tenant_enterprise_id">
            <el-select
              v-model="form.tenant_enterprise_id"
              placeholder="从企业台账选择"
              filterable
              style="width: 100%"
            >
              <el-option
                v-for="e in entOptions"
                :key="e.id"
                :label="e.name"
                :value="e.id"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="承租面积(㎡)">
            <el-input-number
              v-model="form.tenant_area"
              :min="0"
              :precision="2"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="是否签订协议">
            <el-select
              v-model="form.agreement_signed"
              placeholder="请选择"
              style="width: 100%"
              clearable
            >
              <el-option label="已签" :value="true" />
              <el-option label="未签" :value="false" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button
        type="primary"
        :loading="submitLoading"
        @click="handleSubmit(formRef)"
      >
        保存
      </el-button>
    </template>
  </el-dialog>
  </div>
</template>

<style scoped>
.is-unlinked {
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>
