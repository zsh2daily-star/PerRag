<script setup lang="ts">
import { reactive, ref, watch, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  createInspection,
  updateInspection,
  getInspectionList,
  getEnterpriseOptions,
  getInspectorOptions,
  getUnmatchedEnterprises,
  relinkEnterprise,
  checkNoticeExport,
  checkReviewExport,
  downloadNoticeDoc,
  downloadReviewDoc
} from "@/api/inspections";
import type { ExportCheckResult } from "@/api/inspections";
import { getEnterprise } from "@/api/enterprises";
import ReTablePage from "@/components/ReTablePage/index.vue";
import EditableCell from "@/components/EditableCell/index.vue";
import type {
  TablePageConfig,
  TablePageExpose
} from "@/components/ReTablePage/types";
import type {
  FilterGroup,
  FilterLeaf
} from "@/components/ReFilterBuilder/types";
import {
  buildInspectionConfig,
  levelTagType,
  levelText,
  statusTagType,
  statusText
} from "./table.config";
import { fmtDate } from "@/utils/date";
import type { Inspection, Level } from "./table.config";

defineOptions({ name: "Inspections" });

const router = useRouter();

/** 双击企业名跳转到企业档案页 */
const goEnterprise = (row: Inspection) => {
  if (!row.enterprise_id) return;
  router.push({ path: "/enterprise/profile", query: { id: row.enterprise_id } });
};

interface OptionItem {
  id: number;
  name: string;
}

const tableRef = ref<TablePageExpose<Inspection>>();

// ── 同批次同企业的行合并映射：行号 → rowspan（0 表示被合并隐藏）──
const spanMap = ref<number[]>([]);
// 需要垂直合并的列（按 prop 判断，列显隐后索引变化也不受影响）
const mergeProps = [
  "enterprise",
  "inspection_batch",
  "inspectors",
  "responsible"
];

const buildSpanMap = (data: Inspection[]) => {
  const map: number[] = new Array(data.length).fill(0);
  let i = 0;
  while (i < data.length) {
    let size = 1;
    while (
      i + size < data.length &&
      data[i + size].inspection_batch === data[i].inspection_batch &&
      data[i + size].enterprise === data[i].enterprise
    ) {
      size++;
    }
    map[i] = size;
    i += size;
  }
  return map;
};

const spanMethod = ({
  rowIndex,
  column
}: {
  row: Inspection;
  column: any;
  rowIndex: number;
  columnIndex: number;
}) => {
  // 操作列（label="操作"，无 prop）也按批次合并显示
  if (mergeProps.includes(column.property) || column.label === "操作") {
    const span = spanMap.value[rowIndex];
    if (span === 0) return { rowspan: 0, colspan: 0 };
    return { rowspan: span, colspan: 1 };
  }
};

const tableRowClassName = ({ row }: { row: Inspection }) =>
  row.overdue ? "overdue-row" : "";

// ── 未关联企业：筛选开关 + 行内关联 ─────────────────────
const unmatchedCount = ref(0);
const unmatchedOnly = ref(false);
const relinkVisible = ref(false);
const relinkTarget = ref<{ enterprise_name: string } | null>(null);
const relinkEntId = ref<number | undefined>(undefined);
const relinkSaving = ref(false);

const loadUnmatchedCount = async () => {
  try {
    const groups = (await getUnmatchedEnterprises()) || [];
    unmatchedCount.value = groups.reduce((sum, g) => sum + g.record_count, 0);
  } catch {
    // 静默失败，不影响主页面
  }
};

/** 基座经 defineExpose 暴露的 filter（proxyRefs 已解包为 FilterGroup） */
const rootFilter = (): FilterGroup | null =>
  (tableRef.value?.filter as unknown as FilterGroup) ?? null;

/** 切换"仅看未关联企业"筛选：在 filter 树里加减 enterprise_id is_null leaf */
const toggleUnmatchedOnly = (v: boolean | string | number) => {
  const checked = Boolean(v);
  unmatchedOnly.value = checked;
  const group = rootFilter();
  if (!group) return;
  const idx = group.children.findIndex(
    n =>
      n.type === "leaf" &&
      n.field === "enterprise_id" &&
      n.operator === "is_null"
  );
  if (checked) {
    if (idx < 0) {
      group.children.push({
        type: "leaf",
        field: "enterprise_id",
        operator: "is_null",
        value: null
      } as FilterLeaf);
    }
  } else if (idx >= 0) {
    group.children.splice(idx, 1);
  }
  tableRef.value?.refresh();
};

const openRelink = (enterpriseName: string) => {
  relinkTarget.value = { enterprise_name: enterpriseName };
  relinkEntId.value = undefined;
  relinkVisible.value = true;
};

const confirmRelink = async () => {
  if (!relinkTarget.value || !relinkEntId.value) {
    ElMessage.warning("请先选择要关联的企业");
    return;
  }
  relinkSaving.value = true;
  try {
    const res = await relinkEnterprise({
      enterprise_name: relinkTarget.value.enterprise_name,
      enterprise_id: relinkEntId.value
    });
    if (!res.success) {
      if (res.code === "batch_conflict") {
        const batches = (res.data?.conflict_batches || [])
          .map((b: any) => `${b.batch}(${b.count}条)`)
          .join("、");
        ElMessageBox.alert(
          `${res.message}：${batches}。该企业可能在 Excel 中被写成了两个名字，请联系管理员合并。`,
          "关联冲突",
          { type: "warning" }
        );
      } else {
        ElMessage.error(res.message || "关联失败");
      }
      return;
    }
    ElMessage.success(res.message || "关联成功");
    relinkVisible.value = false;
    tableRef.value?.reload();
    loadUnmatchedCount();
  } catch (e: any) {
    ElMessage.error("关联失败：" + (e?.message || "未知错误"));
  } finally {
    relinkSaving.value = false;
  }
};

// ── 表格配置（列/筛选/批量/导入导出全部声明式）──────────
const config: TablePageConfig<Inspection> = buildInspectionConfig({
  spanMethod,
  rowClassName: tableRowClassName,
  onRowsLoaded: rows => {
    spanMap.value = buildSpanMap(rows);
  },
  onImported: loadUnmatchedCount,
  onReset: () => {
    unmatchedOnly.value = false;
  }
});

// ── 新建 / 派整改 下拉选项 ──────────────────────────────
const enterpriseOptions = ref<OptionItem[]>([]);
const inspectorOptions = ref<OptionItem[]>([]);

const fetchOptions = async () => {
  // 两个下拉各自独立加载：检查人接口需要管理员权限，
  // 失败不能拖累企业选项（否则 relink 弹窗的企业下拉会空）
  try {
    enterpriseOptions.value = await getEnterpriseOptions();
  } catch {
    // 企业选项加载失败不阻塞主流程
  }
  try {
    inspectorOptions.value = await getInspectorOptions();
  } catch {
    // 非管理员账号无权访问 /admin/staff/brief，静默跳过
  }
};

// ── 新建 / 编辑 弹窗（复用同一表单）────────────────────
const createVisible = ref(false);
const createLoading = ref(false);
const formRef = ref<FormInstance>();
// create：新建；edit：编辑（同批次同企业的全部隐患一起编辑）
const formMode = ref<"create" | "edit">("create");
const form = reactive({
  enterprise_id: "" as number | "",
  notice_number: "",
  inspect_date: "",
  // 巡查人：可自由键入多个姓名（顿号分隔存库），也可从员工档案下拉选择
  inspector_names: [] as string[],
  // 整改责任人：选企业后自动带入安全员（无则负责人），也可键入任意姓名
  assignee_name: "",
  deadline: ""
});

// ── 多隐患条目：新建一次录入 N 条；编辑回填同批次同企业全部隐患逐条修改 ──
interface HazardEntry {
  /** 编辑模式：已有记录 id（新建条目无 id） */
  id?: number;
  level: Level;
  category: string;
  description: string;
  /** 编辑模式：每条隐患独立状态流转（后端按行存状态） */
  originStatus?: "open" | "rectifying" | "closed";
  status?: "open" | "rectifying" | "closed";
  complete_date?: string;
}
const hazards = ref<HazardEntry[]>([{ level: "low", category: "", description: "" }]);
const addHazard = () => {
  hazards.value.push({ level: "low", category: "", description: "" });
};
const removeHazard = (i: number) => {
  if (hazards.value.length <= 1) return;
  hazards.value.splice(i, 1);
};

const rules: FormRules = {
  enterprise_id: [{ required: true, message: "请选择企业", trigger: "change" }],
  inspect_date: [
    { required: true, message: "请选择巡查日期", trigger: "change" }
  ],
  inspector_names: [
    {
      required: true,
      validator: (_r, v: string[], cb) =>
        v && v.length ? cb() : cb(new Error("请填写巡查人")),
      trigger: "change"
    }
  ]
};

/** 按本地时区取今天 YYYY-MM-DD，避免凌晨 UTC 偏移 */
const todayLocal = () => {
  const n = new Date();
  return [
    n.getFullYear(),
    String(n.getMonth() + 1).padStart(2, "0"),
    String(n.getDate()).padStart(2, "0")
  ].join("-");
};

/** 本地时区 base + N 天的 YYYY-MM-DD */
const plusDays = (base: Date, days: number) => {
  const d = new Date(base);
  d.setDate(d.getDate() + days);
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0")
  ].join("-");
};

const resetForm = () => {
  Object.assign(form, {
    enterprise_id: "",
    notice_number: "",
    inspect_date: todayLocal(),
    inspector_names: [],
    assignee_name: "",
    deadline: plusDays(new Date(), 30)
  });
  hazards.value = [{ level: "low", category: "", description: "" }];
};

// ── 责任人自动带入：选企业 → 安全员 →（兜底）负责人 →（兜底）留空 ──
/** 从「姓名 电话、姓名 电话」类文本中取第一个人的姓名 */
const firstPersonName = (s?: string | null) => {
  if (!s) return "";
  const first = (s.split("、")[0] || "").trim();
  // 姓名是纯中文，剥掉电话数字/空格/括号等噪声
  return first.replace(/[0-9+\-—\s()（）]+/g, "").trim();
};

watch(
  () => form.enterprise_id,
  async id => {
    // 仅新建模式自动带入，编辑不覆盖已有责任人
    if (formMode.value !== "create" || !id) return;
    try {
      const ent: any = await getEnterprise(id as number);
      form.assignee_name =
        firstPersonName(ent?.safety_officer) ||
        firstPersonName(ent?.person_in_charge) ||
        "";
    } catch {
      /* 拉取失败不阻塞表单，责任人留空可手填 */
    }
  }
);

const openCreate = () => {
  formMode.value = "create";
  resetForm();
  createVisible.value = true;
};

/** 责任人解析：姓名匹配员工档案 → 走外键；否则存纯文本（空则双清） */
const resolveAssignee = () => {
  const name = form.assignee_name.trim();
  const staff = inspectorOptions.value.find(o => o.name === name);
  return {
    rect_responsible_user_id: staff ? staff.id : null,
    rect_responsible_name: staff ? "" : name
  };
};

/** 编辑：拉取同批次同企业全部隐患逐条回填（一次巡查可发现多条隐患） */
const openEdit = async (row: Inspection) => {
  formMode.value = "edit";
  // 同批次 + 同企业的全部隐患行（拉取失败退化为仅编辑当前行）
  let group: Inspection[] = [row];
  try {
    const { list } = await getInspectionList({
      page: 1,
      page_size: 300,
      inspection_batch: row.inspection_batch
    });
    const same = list.filter(h => h.enterprise_id === row.enterprise_id);
    if (same.length) group = same;
  } catch {
    /* 退化为单条 */
  }
  Object.assign(form, {
    enterprise_id: row.enterprise_id ?? "",
    notice_number: row.notice_number || "",
    inspect_date: row.inspect_date || "",
    // 巡查人：回填显示原文（inspectors=后端 inspector_name，顿号分隔多人）
    inspector_names: row.inspectors
      ? row.inspectors
          .split(/[、,，]/)
          .map(s => s.trim())
          .filter(Boolean)
      : [],
    // 责任人显示优先员工姓名（后端 rect_responsible_name 已含外键回退）
    assignee_name: row.responsible || "",
    deadline: row.deadline || ""
  });
  hazards.value = group.map(h => ({
    id: h.id,
    level: h.level || "low",
    category: h.category || "",
    description: h.description || "",
    originStatus: h.status,
    status: h.status,
    complete_date:
      (h.completed_at && h.completed_at.slice(0, 10)) ||
      h.review_date ||
      todayLocal()
  }));
  createVisible.value = true;
};

const submitForm = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    // 逐条校验隐患描述（新建/编辑共用多隐患条目）
    const bad = hazards.value.findIndex(h => !h.description.trim());
    if (bad >= 0) {
      ElMessage.warning(`第 ${bad + 1} 条隐患的描述不能为空`);
      return;
    }
    // 编辑模式：已闭环的隐患需填闭环日期
    if (formMode.value === "edit") {
      const noDate = hazards.value.find(
        h => h.status === "closed" && !h.complete_date
      );
      if (noDate) {
        ElMessage.warning("已闭环的隐患需选择闭环日期");
        return;
      }
    }
    createLoading.value = true;
    try {
      if (formMode.value === "create") {
        await createInspection({
          enterprise_id: form.enterprise_id,
          notice_number: form.notice_number || null,
          inspect_date: form.inspect_date,
          inspectors: form.inspector_names
            .map(s => s.trim())
            .filter(Boolean)
            .join("、"),
          ...resolveAssignee(),
          deadline: form.deadline || null,
          hazards: hazards.value.map(h => ({
            hazard_level: h.level,
            hazard_category: h.category.trim(),
            hazard_description: h.description.trim()
          }))
        });
        ElMessage.success("创建成功");
      } else {
        // 编辑：巡查信息/责任人/期限/编号写入同批次同企业全部行，隐患逐行更新
        const shared = {
          inspection_date: form.inspect_date,
          inspectors: form.inspector_names
            .map(s => s.trim())
            .filter(Boolean)
            .join("、"),
          ...resolveAssignee(),
          rect_deadline: form.deadline || null,
          notice_number: form.notice_number || null
        };
        await Promise.all(
          hazards.value.map(h => {
            const payload: Record<string, any> = {
              ...shared,
              hazard_level: h.level,
              hazard_category: h.category,
              hazard_description: h.description
            };
            if (h.status) payload.status = h.status;
            // 闭环时传闭环日期，后端自动同步复查日期
            if (h.status === "closed") {
              payload.rect_completed_at = h.complete_date;
            }
            return updateInspection(h.id!, payload as any);
          })
        );
        ElMessage.success("保存成功");
      }
      createVisible.value = false;
      tableRef.value?.reload();
    } catch (e: any) {
      ElMessage.error(
        e?.response?.data?.detail || e?.message || "操作失败"
      );
    } finally {
      createLoading.value = false;
    }
  });
};

// ── 文书导出弹窗（交办单 / 复查意见书）──────────────────
const exportVisible = ref(false);
// 字段检查 loading（打开弹窗时并行调两个 check 接口）
const exportCheckLoading = ref(false);
// 导出动作 loading（区分哪个按钮在转圈，避免误读）
const exportLoadingType = ref<"notice" | "review" | null>(null);
const exportRow = ref<Inspection | null>(null);
const noticeCheck = ref<ExportCheckResult | null>(null);
const reviewCheck = ref<ExportCheckResult | null>(null);
// 可编辑的下载文件名（用户可改）
const noticeFilename = ref("");
const reviewFilename = ref("");

const openExport = async (row: Inspection) => {
  exportRow.value = row;
  exportVisible.value = true;
  exportCheckLoading.value = true;
  noticeCheck.value = null;
  reviewCheck.value = null;
  // 默认文件名：文书类型-企业-（编号或批次）
  const ent = row.enterprise || "未填企业";
  const suffix = row.notice_number || row.inspection_batch || String(row.id);
  noticeFilename.value = `交办单-${ent}-${suffix}.docx`;
  reviewFilename.value = `复查意见书-${ent}-${suffix}.docx`;
  try {
    const [n, r] = await Promise.allSettled([
      checkNoticeExport(row.id),
      checkReviewExport(row.id)
    ]);
    if (n.status === "fulfilled") noticeCheck.value = n.value;
    if (r.status === "fulfilled") reviewCheck.value = r.value;
  } finally {
    exportCheckLoading.value = false;
  }
};

const handleExport = async (type: "notice" | "review") => {
  if (!exportRow.value) return;
  const row = exportRow.value;
  exportLoadingType.value = type;
  try {
    if (type === "notice") {
      await downloadNoticeDoc(row.id, row.enterprise, noticeFilename.value);
      ElMessage.success("交办单已导出");
    } else {
      await downloadReviewDoc(row.id, row.enterprise, reviewFilename.value);
      ElMessage.success("复查意见书已导出");
    }
  } catch (e: any) {
    // 用户取消保存对话框时不弹错误提示
    if (e?.message === "已取消保存") return;
    ElMessage.error(e?.message || "导出失败");
  } finally {
    exportLoadingType.value = null;
  }
};

// ── 详情弹窗状态 ──
const detailVisible = ref(false);
const detailRow = ref<Inspection | null>(null);
const batchHazards = ref<Inspection[]>([]);
const batchLoading = ref(false);

/** 同批次隐患是否跨多家企业（决定每条描述前要不要额外标企业名） */
const hasMultiEnterpriseBatch = () => {
  const ents = new Set(batchHazards.value.map(h => h.enterprise));
  return ents.size > 1;
};

const openDetail = async (row: Inspection) => {
  detailRow.value = row;
  batchHazards.value = [];
  detailVisible.value = true;

  // ── 拉同批次全部隐患（可能跨多企业）──
  batchLoading.value = true;
  try {
    const { list } = await getInspectionList({
      page: 1,
      page_size: 300,
      inspection_batch: row.inspection_batch
    });
    batchHazards.value = list;
  } catch {
    batchHazards.value = [];
  } finally {
    batchLoading.value = false;
  }
};

onMounted(() => {
  fetchOptions();
  loadUnmatchedCount();
});
</script>

<template>
  <div>
    <ReTablePage ref="tableRef" :config="config">
    <!-- 工具栏：新建巡查单 -->
    <template #toolbar-extra>
      <el-button type="primary" @click="openCreate">新建巡查单</el-button>
    </template>

    <!-- 企业列表头筛选弹窗追加：仅看未关联企业 -->
    <template #header-extra="{ column }">
      <div v-if="column.key === 'enterprise'" class="mt-2">
        <el-checkbox
          :model-value="unmatchedOnly"
          @update:model-value="toggleUnmatchedOnly"
        >
          仅看未关联企业
          <span v-if="unmatchedCount > 0" class="text-orange-500 font-bold">
            （{{ unmatchedCount }}）
          </span>
        </el-checkbox>
      </div>
    </template>

    <!-- 自定义单元格 -->
    <template #cell-enterprise="{ row }: { row: Inspection }">
      <span
        :class="[
          'ent-name',
          row.enterprise_id ? 'is-link' : 'text-orange-500 font-bold'
        ]"
        :title="row.enterprise_id ? '双击查看企业档案' : ''"
        @dblclick="goEnterprise(row)"
      >
        {{ row.enterprise }}
      </span>
      <el-tag
        v-if="!row.enterprise_id"
        type="warning"
        size="small"
        effect="plain"
        class="ml-1"
      >
        未关联
      </el-tag>
      <el-button
        v-if="!row.enterprise_id"
        type="primary"
        size="small"
        link
        class="ml-1"
        @click="openRelink(row.enterprise)"
      >
        关联
      </el-button>
    </template>

    <template #cell-description="{ row }: { row: Inspection }">
      <div class="flex items-center gap-1">
        <el-tag
          v-if="row.hazard_count_total > 1"
          size="small"
          type="info"
          effect="plain"
        >
          {{ row.hazard_index }}/{{ row.hazard_count_total }}
        </el-tag>
        <EditableCell
          :value="row.description"
          label="隐患描述"
          :update-fn="async (v: string) => {
            await updateInspection(row.id, { hazard_description: v } as any);
            row.description = v;
          }"
        />
      </div>
    </template>

    <template #cell-level="{ row }: { row: Inspection }">
      <el-tag :type="levelTagType(row.level)" size="small">
        {{ levelText(row.level) }}
      </el-tag>
    </template>

    <template #cell-review-date="{ row }: { row: Inspection }">
      <span v-if="row.review_date">{{ fmtDate(row.review_date) }}</span>
      <span v-else class="text-gray-400">—</span>
    </template>

    <template #cell-status="{ row }: { row: Inspection }">
      <span v-if="row.completed_at">{{
        fmtDate(row.completed_at)
      }}</span>
      <template v-else>
        <el-tag v-if="row.overdue" type="danger" size="small">超期</el-tag>
        <el-tag v-else :type="statusTagType[row.status]" size="small">
          {{ statusText[row.status] }}
        </el-tag>
      </template>
    </template>

    <!-- 操作列：详情 / 派整改 / 编辑 / 导出文书 -->
    <template #actions="{ row }: { row: Inspection }">
      <el-button link type="primary" size="small" @click="openDetail(row)">
        详情
      </el-button>
      <el-button link type="primary" size="small" @click="openEdit(row)">
        编辑
      </el-button>
      <el-button link type="success" size="small" @click="openExport(row)">
        导出
      </el-button>
    </template>

    <!-- 导入结果（本页格式：total_counts + 未匹配企业 + 分 sheet 明细） -->
    <template #import-result="{ result }">
      <template v-if="result.total_counts">
        <el-alert type="success" :closable="false" class="mb-4">
          新增 {{ result.total_counts.created }} 条， 更新
          {{ result.total_counts.updated }} 条， 跳过
          {{ result.total_counts.skipped }} 条， 失败
          {{ result.total_counts.failed }} 条
        </el-alert>

        <div v-if="result.unmatched_enterprises?.length" class="mb-4">
          <div class="mb-2 text-sm font-bold text-orange-600">
            未匹配企业（{{ result.unmatched_enterprises.length }}
            家，记录已保留但未关联企业档案）：
          </div>
          <div
            v-for="item in result.unmatched_enterprises"
            :key="item.name"
            class="mb-1 flex items-start gap-1 text-sm"
          >
            <el-tag type="warning" effect="plain" class="shrink-0">
              {{ item.name }}
            </el-tag>
            <span v-if="item.suggestion" class="text-gray-500">
              疑似：<span class="text-orange-600">{{ item.suggestion }}</span>
              （相似度 {{ Math.round(item.score * 100) }}%，请人工核实）
            </span>
            <span v-else class="text-gray-400"
              >企业库无相近名称，需补登档案</span
            >
          </div>
        </div>

        <el-table :data="result.sheets" border size="small">
          <el-table-column prop="sheet" label="Sheet" min-width="120" />
          <el-table-column
            prop="raw_rows"
            label="原始行"
            width="80"
            align="center"
          />
          <el-table-column
            prop="expanded_hazards"
            label="拆分隐患"
            width="90"
            align="center"
          />
          <el-table-column label="新增" width="60" align="center">
            <template #default="{ row }">{{
              row.counts?.created || 0
            }}</template>
          </el-table-column>
          <el-table-column label="更新" width="60" align="center">
            <template #default="{ row }">{{
              row.counts?.updated || 0
            }}</template>
          </el-table-column>
          <el-table-column label="跳过" width="60" align="center">
            <template #default="{ row }">{{
              row.counts?.skipped || 0
            }}</template>
          </el-table-column>
          <el-table-column label="失败" width="60" align="center">
            <template #default="{ row }">{{
              row.counts?.failed || 0
            }}</template>
          </el-table-column>
        </el-table>
      </template>
      <el-alert
        v-else
        type="error"
        :title="result.message || '导入失败'"
        :closable="false"
      />
    </template>
  </ReTablePage>

  <!-- 新建 / 编辑巡查单弹窗 -->
  <el-dialog
    v-model="createVisible"
    :title="formMode === 'create' ? '新建巡查单' : '编辑巡查单'"
    width="640px"
    destroy-on-close
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
      <!-- 归属 -->
      <el-divider content-position="left">归属</el-divider>
      <el-form-item label="企业" prop="enterprise_id">
        <el-select
          v-model="form.enterprise_id"
          placeholder="请选择企业"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="item in enterpriseOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>

      <!-- 巡查 -->
      <el-divider content-position="left">巡查</el-divider>
      <el-form-item label="巡查日期" prop="inspect_date">
        <el-date-picker
          v-model="form.inspect_date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="请选择"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="巡查人" prop="inspector_names">
        <el-select
          v-model="form.inspector_names"
          multiple
          filterable
          allow-create
          default-first-option
          :reserve-keyword="false"
          placeholder="输入或选择姓名，可多人（回车确认）"
          style="width: 100%"
        >
          <el-option
            v-for="item in inspectorOptions"
            :key="item.id"
            :label="item.name"
            :value="item.name"
          />
        </el-select>
      </el-form-item>

      <!-- 隐患：新建一次录入多条；编辑回填同批次同企业全部隐患逐条修改 -->
      <el-divider content-position="left">隐患</el-divider>
      <div
        v-for="(h, i) in hazards"
        :key="h.id ?? `new-${i}`"
        class="hazard-entry"
      >
        <div class="hazard-entry__head">
          <span class="hazard-entry__no">隐患 {{ i + 1 }}</span>
          <el-button
            v-if="formMode === 'create' && hazards.length > 1"
            link
            type="danger"
            size="small"
            @click="removeHazard(i)"
          >
            删除
          </el-button>
        </div>
        <el-form-item label="等级">
          <el-select v-model="h.level" style="width: 100%">
            <el-option label="一般隐患" value="low" />
            <el-option label="较大隐患" value="medium" />
            <el-option label="重大隐患" value="high" />
          </el-select>
        </el-form-item>
        <el-form-item label="类别">
          <el-input v-model="h.category" placeholder="可留空" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="h.description"
            type="textarea"
            :rows="3"
            placeholder="请输入隐患描述"
          />
        </el-form-item>
        <!-- 编辑模式：每条隐患状态独立流转（后端按行存状态） -->
        <template v-if="formMode === 'edit'">
          <el-form-item label="整改状态">
            <el-select v-model="h.status" style="width: 100%">
              <el-option-group label="整改状态">
                <el-option
                  v-if="h.originStatus === 'open'"
                  label="待处理"
                  value="open"
                />
                <el-option
                  v-if="h.originStatus !== 'closed'"
                  label="整改中"
                  value="rectifying"
                />
              </el-option-group>
              <el-option-group label="整改完成">
                <el-option
                  v-if="
                    h.originStatus === 'rectifying' ||
                    h.originStatus === 'closed'
                  "
                  label="已闭环"
                  value="closed"
                />
              </el-option-group>
            </el-select>
          </el-form-item>
          <el-form-item
            v-if="h.status === 'closed'"
            label="闭环日期"
            required
          >
            <el-date-picker
              v-model="h.complete_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择实际闭环日期"
              style="width: 100%"
            />
          </el-form-item>
        </template>
      </div>
      <el-button
        v-if="formMode === 'create'"
        class="hazard-entry__add"
        plain
        @click="addHazard"
      >
        ＋ 添加一条隐患
      </el-button>

      <!-- 可选信息（默认折叠到最后）：交办单编号 + 整改责任人/期限 -->
      <el-divider content-position="left">可选信息（交办单 / 整改）</el-divider>
      <el-form-item label="编号">
        <el-input
          v-model="form.notice_number"
          placeholder="交办单编号（可留空，同批次自动同步）"
        />
      </el-form-item>
      <el-form-item label="责任人">
        <el-select
          v-model="form.assignee_name"
          placeholder="可留空，选企业后自动带入安全员/负责人"
          filterable
          allow-create
          default-first-option
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="item in inspectorOptions"
            :key="item.id"
            :label="item.name"
            :value="item.name"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="期限">
        <el-date-picker
          v-model="form.deadline"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="可留空"
          style="width: 100%"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="createVisible = false">取消</el-button>
      <el-button
        type="primary"
        :loading="createLoading"
        @click="submitForm(formRef)"
      >
        确定
      </el-button>
    </template>
  </el-dialog>

  <!-- 详情弹窗 -->
  <el-dialog
    v-model="detailVisible"
    title="隐患详情"
    width="740px"
    destroy-on-close
  >
    <template v-if="detailRow">
      <!-- 基本信息 -->
      <el-descriptions :column="3" border size="small" class="mb-4">
        <el-descriptions-item label="企业" :span="2">
          {{ detailRow.enterprise }}
          <span
            v-if="detailRow.enterprise_code"
            class="ml-2 text-xs text-gray-400"
          >
            {{ detailRow.enterprise_code }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="批次">
          {{ detailRow.inspection_batch }}
          <span
            v-if="detailRow.hazard_count_total > 1"
            class="ml-2 text-xs text-gray-400"
          >
            （{{ detailRow.hazard_index }}/{{ detailRow.hazard_count_total }}）
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="园区">
          {{ detailRow.region || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="行业">
          {{ detailRow.industry || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="检查日期">
          {{ detailRow.inspect_date }}
        </el-descriptions-item>
        <el-descriptions-item label="检查人">
          {{ detailRow.inspectors || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="整改责任人">
          {{ detailRow.responsible || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="隐患等级">
          <el-tag :type="levelTagType(detailRow.level)" size="small">
            {{ levelText(detailRow.level) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusTagType[detailRow.status]" size="small">
            {{ statusText[detailRow.status] }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="复查时间" :span="2">
          <span v-if="detailRow.review_date">{{
            fmtDate(detailRow.review_date)
          }}</span>
          <span v-else class="text-gray-400">—</span>
        </el-descriptions-item>
        <el-descriptions-item label="整改完成时间" :span="2">
          <span v-if="detailRow.completed_at">{{
            fmtDate(detailRow.completed_at)
          }}</span>
          <template v-else>
            <el-tag v-if="detailRow.overdue" type="danger" size="small">
              超期未完成（期限 {{ detailRow.deadline || "30天" }}）
            </el-tag>
            <el-tag v-else-if="detailRow.deadline" type="warning" size="small">
              待整改（期限 {{ detailRow.deadline }}）
            </el-tag>
            <el-tag v-else type="warning" size="small" effect="plain">
              待整改
            </el-tag>
          </template>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 隐患描述（同批次全部隐患分行拼接） -->
      <div class="mb-4">
        <div class="mb-1 text-sm font-bold text-gray-600">
          隐患描述
          <span v-if="batchHazards.length > 1" class="ml-2 text-xs font-normal text-gray-400">
            （本批次共 {{ batchHazards.length }} 条隐患，按序号）
          </span>
        </div>
        <div
          v-loading="batchLoading && batchHazards.length === 0"
          class="rounded bg-gray-50 p-3 text-sm leading-relaxed"
        >
          <div
            v-for="h in batchHazards"
            :key="h.id"
            :class="[
              'py-1.5',
              batchHazards.length > 1 ? 'border-b border-dashed border-gray-200 last:border-0' : '',
              h.id === detailRow?.id ? 'text-primary font-semibold' : ''
            ]"
          >
            <!-- 序号 -->
            <span class="mr-1 inline-flex w-5 text-gray-500">{{ h.hazard_index }}.</span>
            <!-- 等级 tag -->
            <el-tag
              :type="levelTagType(h.level)"
              size="small"
              class="mr-1 align-middle"
            >{{ levelText(h.level) }}</el-tag>
            <!-- 企业（多企业批次才显示） -->
            <template v-if="hasMultiEnterpriseBatch">
              <span class="mr-1 align-middle text-xs text-orange-600">
                {{ h.enterprise }}
              </span>
            </template>
            <!-- 描述 -->
            <span class="align-middle">{{ h.description }}</span>
            <!-- 状态 tag -->
            <el-tag
              v-if="h.status !== 'open' || batchHazards.length > 1"
              :type="statusTagType[h.status]"
              size="small"
              effect="plain"
              class="ml-1 align-middle"
            >{{ statusText[h.status] }}</el-tag>
          </div>
          <!-- 兜底：batch 拉取失败或只有当前行 -->
          <div v-if="!batchLoading && batchHazards.length === 0 && detailRow" class="py-1.5">
            {{ detailRow.description }}
          </div>
        </div>
      </div>

      <!-- 备注 -->
      <div v-if="detailRow.remark" class="mb-4">
        <div class="mb-1 text-sm font-bold text-gray-600">备注</div>
        <div class="rounded bg-orange-50 p-3 text-sm leading-relaxed">
          {{ detailRow.remark }}
        </div>
      </div>
    </template>
  </el-dialog>

  <!-- 选择目标企业 -->
  <el-dialog
    v-model="relinkVisible"
    title="关联到企业档案"
    width="520px"
    append-to-body
  >
    <div v-if="relinkTarget" class="text-sm">
      <div class="mb-3">
        <span class="text-gray-500">Excel 企业名：</span>
        <el-tag type="warning" effect="plain">
          {{ relinkTarget.enterprise_name }}
        </el-tag>
      </div>
      <div class="mb-2 text-gray-500">
        选择企业档案库中的对应企业（该名称下所有批次记录将一并关联）：
      </div>
      <el-select
        v-model="relinkEntId"
        filterable
        clearable
        placeholder="输入企业名称搜索"
        style="width: 100%"
      >
        <el-option
          v-for="item in enterpriseOptions"
          :key="item.id"
          :label="item.name"
          :value="item.id"
        />
      </el-select>
    </div>
    <template #footer>
      <el-button @click="relinkVisible = false">取消</el-button>
      <el-button type="primary" :loading="relinkSaving" @click="confirmRelink">
        确认关联
      </el-button>
    </template>
  </el-dialog>

  <!-- 文书导出弹窗：交办单 / 复查意见书，缺失字段时灰化"直接导出" -->
  <el-dialog
    v-model="exportVisible"
    title="导出文书"
    width="540px"
    :close-on-click-modal="false"
  >
    <div v-loading="exportCheckLoading" element-loading-text="正在检查字段...">
      <div v-if="exportRow" class="mb-4 text-sm text-gray-600">
        <span class="font-bold">{{ exportRow.enterprise }}</span>
        <span class="ml-2">{{ exportRow.inspect_date || "无巡查日期" }}</span>
        <span class="ml-2 text-xs">批次：{{ exportRow.inspection_batch }}</span>
      </div>

      <!-- 交办单 -->
      <div class="border rounded p-3 mb-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="font-bold">问题隐患交办单</span>
            <el-tag
              v-if="noticeCheck"
              :type="noticeCheck.ready ? 'success' : 'warning'"
              size="small"
            >
              {{ noticeCheck.ready ? "可导出" : "有条件不满足" }}
            </el-tag>
            <el-tag v-else type="info" size="small">检查中</el-tag>
          </div>
          <el-button
            :type="noticeCheck?.ready ? 'primary' : 'info'"
            size="small"
            :loading="exportLoadingType === 'notice'"
            @click="handleExport('notice')"
          >
            直接导出
          </el-button>
        </div>
        <div class="mt-2 flex items-center gap-2">
          <span class="text-xs text-gray-500 whitespace-nowrap">文件名：</span>
          <el-input v-model="noticeFilename" size="small" />
        </div>
        <div
          v-if="noticeCheck && !noticeCheck.ready"
          class="mt-2 text-xs text-orange-600"
        >
          {{ noticeCheck.missing.join("、") }}（灰色按钮仍可强制导出）
        </div>
      </div>

      <!-- 复查意见书 -->
      <div class="border rounded p-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="font-bold">整改复查意见书</span>
            <el-tag
              v-if="reviewCheck"
              :type="reviewCheck.ready ? 'success' : 'warning'"
              size="small"
            >
              {{ reviewCheck.ready ? "可导出" : "有条件不满足" }}
            </el-tag>
            <el-tag v-else type="info" size="small">检查中</el-tag>
          </div>
          <el-button
            :type="reviewCheck?.ready ? 'primary' : 'info'"
            size="small"
            :loading="exportLoadingType === 'review'"
            @click="handleExport('review')"
          >
            直接导出
          </el-button>
        </div>
        <div class="mt-2 flex items-center gap-2">
          <span class="text-xs text-gray-500 whitespace-nowrap">文件名：</span>
          <el-input v-model="reviewFilename" size="small" />
        </div>
        <div
          v-if="reviewCheck && !reviewCheck.ready"
          class="mt-2 text-xs text-orange-600"
        >
          {{ reviewCheck.missing.join("、") }}（灰色按钮仍可强制导出）
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="exportVisible = false">关闭</el-button>
    </template>
  </el-dialog>
  </div>
</template>

<style scoped>
.ent-name.is-link {
  color: var(--el-color-primary);
  cursor: pointer;
}
.ent-name.is-link:hover {
  text-decoration: underline;
}

:deep(.overdue-row) {
  background-color: var(--el-color-danger-light-9);
}

/* 新建弹窗：多隐患条目卡片 */
.hazard-entry {
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 6px;
  padding: 8px 12px 0;
  margin-bottom: 4px;
  background: var(--el-fill-color-lighter, #fafafa);
}
.hazard-entry__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.hazard-entry__no {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary, #909399);
}
.hazard-entry__add {
  width: 100%;
  margin-bottom: 8px;
}
</style>
