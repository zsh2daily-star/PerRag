<script setup lang="ts">
import { reactive, ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  createHazardousChemical,
  updateHazardousChemical
} from "@/api/hazardousChemicals";
import type { HazardousChemical } from "@/api/hazardousChemicals";
import { getEnterpriseList } from "@/api/enterprises";
import ReTablePage from "@/components/ReTablePage/index.vue";
import type {
  TablePageConfig,
  TablePageExpose
} from "@/components/ReTablePage/types";
import { buildHazchemConfig } from "./table.config";

defineOptions({ name: "HazardousChemicals" });

const tableRef = ref<TablePageExpose<HazardousChemical>>();

// ── 合并单元格：同一企业的多行，企业相关列纵向合并 ──
/** 需要按企业合并的列 prop 集合 */
const ENTERPRISE_MERGE_PROPS = new Set([
  "enterprise_name",
  "enterprise_region",
  "town",
  "enterprise_industry"
]);

/** 企业分组键：优先用外键 ID，回退名称 */
const enterpriseKey = (r: HazardousChemical): string =>
  r.enterprise_id != null
    ? `id:${r.enterprise_id}`
    : `name:${r.enterprise_name ?? ""}`;

/** 每行的合并信息：rowIndex → { rowspan, isStart } */
const mergeInfo = ref<Record<number, { rowspan: number; isStart: boolean }>>(
  {}
);

/** 按企业稳定排序，保证同一企业的多行连续（组内保持后端原顺序） */
const sortByEnterprise = (rows: HazardousChemical[]): HazardousChemical[] => {
  const stable = rows.map((r, i) => ({ r, i }));
  stable.sort((a, b) => {
    const ka = enterpriseKey(a.r);
    const kb = enterpriseKey(b.r);
    return ka !== kb ? ka.localeCompare(kb) : a.i - b.i;
  });
  return stable.map(x => x.r);
};

/** 依据当前数据预计算企业分组的合并信息 */
const computeMergeInfo = (rows: HazardousChemical[]) => {
  const info: Record<number, { rowspan: number; isStart: boolean }> = {};
  let i = 0;
  while (i < rows.length) {
    const cur = enterpriseKey(rows[i]);
    let count = 1;
    while (i + count < rows.length && enterpriseKey(rows[i + count]) === cur) {
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

/** el-table span-method：企业列按分组合并 */
const spanMethod = ({
  column,
  rowIndex
}: {
  row: HazardousChemical;
  column: any;
  rowIndex: number;
  columnIndex: number;
}) => {
  if (ENTERPRISE_MERGE_PROPS.has(column?.property)) {
    const info = mergeInfo.value[rowIndex];
    if (!info) return { rowspan: 1, colspan: 1 };
    return info.isStart
      ? { rowspan: info.rowspan, colspan: 1 }
      : { rowspan: 0, colspan: 0 };
  }
};

// ── 表格配置（列/筛选/批量全部声明式）──────────
const config: TablePageConfig<HazardousChemical> = buildHazchemConfig({
  spanMethod,
  transformRows: sortByEnterprise,
  onRowsLoaded: computeMergeInfo
});

// ── 企业下拉选项（数据源 = Enterprise 主表；region/industry/scale 随主表，不在本表单维护）──
const entOptions = ref<{ id: number; name: string }[]>([]);
const fetchEntOptions = async () => {
  try {
    const { list } = await getEnterpriseList({ page: 1, page_size: 1000 });
    entOptions.value = (list ?? []).map(e => ({ id: e.id, name: e.name }));
  } catch {
    entOptions.value = [];
  }
};
onMounted(fetchEntOptions);

// ── 新建/编辑弹窗 ───────────────────────────────────────
const dialogVisible = ref(false);
const dialogTitle = ref("新建记录");
const submitLoading = ref(false);
const formRef = ref<FormInstance>();
const isEdit = ref(false);

interface HazchemForm {
  id: number;
  enterprise_id: number | null;
  town: string;
  hazchem_name: string;
  hazchem_state: string;
  reserve: string;
  max_reserve: string;
  annual_usage: string;
  usage: string;
  storage_method: string;
  storage_location: string;
  major_hazard: string;
  remark: string;
}

const emptyForm = (): HazchemForm => ({
  id: 0,
  enterprise_id: null,
  town: "",
  hazchem_name: "",
  hazchem_state: "",
  reserve: "",
  max_reserve: "",
  annual_usage: "",
  usage: "",
  storage_method: "",
  storage_location: "",
  major_hazard: "",
  remark: ""
});

const form = reactive<HazchemForm>(emptyForm());

const rules: FormRules = {
  enterprise_id: [
    { required: true, message: "请选择企业", trigger: "change" }
  ],
  hazchem_name: [
    { required: true, message: "请输入危化品名称", trigger: "blur" }
  ]
};

const openCreate = () => {
  isEdit.value = false;
  dialogTitle.value = "新建记录";
  Object.assign(form, emptyForm());
  dialogVisible.value = true;
};

const openEdit = (row: HazardousChemical) => {
  isEdit.value = true;
  dialogTitle.value = "编辑记录";
  Object.assign(form, emptyForm(), {
    id: row.id,
    enterprise_id: row.enterprise_id,
    town: row.town ?? "",
    hazchem_name: row.hazchem_name ?? "",
    hazchem_state: row.hazchem_state ?? "",
    reserve: row.reserve ?? "",
    max_reserve: row.max_reserve ?? "",
    annual_usage: row.annual_usage ?? "",
    usage: row.usage ?? "",
    storage_method: row.storage_method ?? "",
    storage_location: row.storage_location ?? "",
    major_hazard: row.major_hazard ?? "",
    remark: row.remark ?? ""
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
        await updateHazardousChemical(form.id, payload);
        ElMessage.success("更新成功");
      } else {
        await createHazardousChemical(payload);
        ElMessage.success("新建成功");
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
</script>

<template>
  <div>
    <ReTablePage ref="tableRef" :config="config">
      <!-- 工具栏：新建记录 -->
      <template #toolbar-extra>
        <el-button type="primary" @click="openCreate">新建记录</el-button>
      </template>

      <!-- 企业名单元格（来自 Enterprise 主表，只读展示） -->
      <template #cell-enterprise_name="{ row }: { row: HazardousChemical }">
        <span :class="{ 'is-unlinked': !row.enterprise_id }">
          {{ row.enterprise_name || "—" }}
        </span>
      </template>

      <!-- 重大危险源：不构成=灰色、构成=红色 -->
      <template #cell-major_hazard="{ row }: { row: HazardousChemical }">
        <el-tag
          v-if="row.major_hazard"
          size="small"
          :type="row.major_hazard.includes('不构成') ? 'info' : 'danger'"
          effect="plain"
        >
          {{ row.major_hazard }}
        </el-tag>
        <span v-else>—</span>
      </template>

      <!-- 操作列：编辑 + 删除（删除按钮由基座默认渲染） -->
      <template #actions-prepend="{ row }: { row: HazardousChemical }">
        <el-button link type="primary" size="small" @click="openEdit(row)">
          编辑
        </el-button>
      </template>
    </ReTablePage>

    <!-- 新建/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="820px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="140px"
        label-position="right"
      >
        <el-divider content-position="left">企业信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="所属企业" prop="enterprise_id">
              <el-select
                v-model="form.enterprise_id"
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
            <el-form-item label="镇（区、街道）">
              <el-input v-model="form.town" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">危化品明细</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="危化品名称" prop="hazchem_name">
              <el-input v-model="form.hazchem_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="危化品状态">
              <el-select
                v-model="form.hazchem_state"
                placeholder="请选择"
                style="width: 100%"
                clearable
                filterable
                allow-create
              >
                <el-option label="液态" value="液态" />
                <el-option label="固态" value="固态" />
                <el-option label="气态" value="气态" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="储量">
              <el-input v-model="form.reserve" placeholder="如 20t" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="设计最大储量">
              <el-input v-model="form.max_reserve" placeholder="如 25t" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="年度使用量">
              <el-input v-model="form.annual_usage" placeholder="如 230t" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="危化品用途">
              <el-input v-model="form.usage" placeholder="如 生产" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="存储方式">
              <el-input v-model="form.storage_method" placeholder="如 危化品库" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="存储位置">
              <el-input v-model="form.storage_location" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="是否构成重大危险源">
              <el-input
                v-model="form.major_hazard"
                placeholder="如 不构成重大危险源"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="备注">
              <el-input v-model="form.remark" />
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
