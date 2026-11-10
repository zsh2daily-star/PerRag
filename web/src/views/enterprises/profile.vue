<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowLeft, Search } from "@element-plus/icons-vue";
import {
  getEnterpriseProfile,
  getEnterprises,
  updateEnterprise
} from "@/api/enterprises";
import type { Enterprise, EnterpriseProfile } from "@/api/enterprises";
import {
  categoryTagType,
  safetyFlags,
  extraEntries,
  fireLevelOptions,
  confinedSpaceOptions
} from "./table.config";
import { fmtDate } from "@/utils/date";

defineOptions({ name: "EnterpriseProfile" });

const route = useRoute();
const router = useRouter();

const loading = ref(false);
const profile = ref<EnterpriseProfile | null>(null);

/** 企业 id 走 query（?id=123），未指定时展示企业选择器 */
const enterpriseId = computed(() => Number(route.query.id ?? 0) || 0);
const hasId = computed(() => enterpriseId.value > 0);
const ent = computed(() => profile.value?.enterprise ?? null);

async function load() {
  if (!hasId.value) {
    profile.value = null;
    await searchEnterprises();
    return;
  }
  loading.value = true;
  try {
    const data = await getEnterpriseProfile(enterpriseId.value);
    if (!data) {
      ElMessage.error("企业不存在或已被删除");
      profile.value = null;
      return;
    }
    profile.value = data;
  } catch {
    ElMessage.error("加载企业档案失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(enterpriseId, load);

function goBack() {
  router.back();
}

// ── 企业选择器（无 id 时）──────────────────────────────────
const pickerKeyword = ref("");
const pickerLoading = ref(false);
const pickerList = ref<Enterprise[]>([]);

async function searchEnterprises() {
  pickerLoading.value = true;
  try {
    const { list } = await getEnterprises({
      page: 1,
      // 一次拉全量，保证选择器展示所有企业（后端对 page_size 无上限）
      page_size: 2000,
      keyword: pickerKeyword.value.trim() || undefined
    });
    pickerList.value = list;
  } catch {
    ElMessage.error("查询企业失败");
  } finally {
    pickerLoading.value = false;
  }
}

function selectEnterprise(row: Enterprise) {
  router.push({ name: "EnterpriseProfile", query: { id: row.id } });
}

// ── 展示辅助 ──────────────────────────────────────────────
const dash = (v: unknown) =>
  v === null || v === undefined || v === "" ? "—" : (v as string);

const boolText = (v: boolean | null | undefined) =>
  v === null || v === undefined ? "—" : v ? "是" : "否";

/** 安全风险画像：有限空间 + 8 项风险点布尔标签（与列表「风险标记」列口径一致） */
const riskFlags = computed<string[]>(() => {
  const e = ent.value;
  if (!e) return [];
  const list: string[] = [];
  if ((e.confined_space || []).length) list.push("有限空间");
  for (const f of safetyFlags) {
    if ((e as any)[f.key]) list.push(f.label);
  }
  return list;
});

const extraList = computed(() =>
  ent.value ? extraEntries(ent.value) : []
);

const isSmallVenue = computed(
  () => (ent.value?.enterprise_category || []).includes("小场所")
);

const hasConfinedSpace = computed(() => {
  const e = ent.value;
  if (!e) return false;
  return (
    (e.confined_space || []).length > 0 ||
    !!e.confined_space_count ||
    !!e.confined_space_type ||
    !!e.confined_space_hazards
  );
});

const hasHazchemInfo = computed(() => {
  const e = ent.value;
  if (!e) return false;
  return (
    e.has_hazardous_chemicals ||
    !!e.hazchem_names ||
    (profile.value?.hazardous_chemicals?.length ?? 0) > 0
  );
});

// ── 关联记录 ──────────────────────────────────────────────
const inspections = computed(() => profile.value?.inspections ?? []);
const chemicals = computed(() => profile.value?.hazardous_chemicals ?? []);
const materials = computed(() => profile.value?.materials ?? []);

/** 厂中厂关系：标注该企业在每行中的角色，并派生按角色取的展示字段 */
const factoryRelations = computed(() => {
  const e = ent.value;
  if (!e) return [];
  return (profile.value?.factory_relations ?? []).map(r => {
    const isLessor = r.lessor_enterprise_id === e.id;
    return {
      ...r,
      role: isLessor ? "出租方" : "承租方",
      counterParty: isLessor ? dash(r.tenant_name) : dash(r.lessor_name),
      hazard: isLessor
        ? dash(r.lessor_hazard_description)
        : dash(r.tenant_hazard_description),
      rectStatus: isLessor ? dash(r.lessor_rect_status) : dash(r.tenant_rect_status),
      // 按角色取的补充字段
      area: isLessor ? dash(r.lessor_use_area) : (r.tenant_area ? `${r.tenant_area}㎡` : "—"),
      contactPerson: isLessor ? dash(r.lessor_contact_person) : dash(r.tenant_contact_person),
      contactPhone: isLessor ? dash(r.lessor_contact_phone) : dash(r.tenant_contact_phone),
    };
  });
});

const levelTagType = (l: string | null) =>
  l === "high" ? "danger" : l === "medium" ? "warning" : "info";
const levelText = (l: string | null) =>
  ({ high: "重大", medium: "较大", low: "一般" } as Record<string, string>)[
    l ?? ""
  ] ?? (l || "—");

const inspStatusText = (s: string) =>
  ({ open: "未整改", rectifying: "整改中", closed: "已闭环" } as Record<
    string,
    string
  >)[s] ?? s;
const inspStatusTagType = (s: string) =>
  s === "closed" ? "success" : s === "rectifying" ? "warning" : "danger";

/** 未闭环且整改期限已过 → 超期（派生状态，不落库） */
const isInspOverdue = (row: { status?: string | null; rect_deadline?: string | null }) => {
  if (!row || row.status === "closed" || !row.rect_deadline) return false;
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
  return String(row.rect_deadline).slice(0, 10) < today;
};

/** 不构成重大危险源 → 灰色 info；否则红色 danger */
const majorHazardTagType = (v: string | null) =>
  v && v.includes("不构成") ? "info" : "danger";

// ── 材料状态 ──
const matStatusText = (s: string) =>
  ({ pending: "待索引", indexing: "索引中", indexed: "已入库", failed: "失败" } as Record<string, string>)[s] ?? s;
const matStatusTagType = (s: string) =>
  s === "indexed" ? "success" : s === "indexing" ? "warning" : s === "failed" ? "danger" : "info";

/** 材料"查看"按钮：跳转到材料库搜索该 uid 或下载 */
const openMaterial = (row: { uid: string; filename: string }) => {
  // 优先尝试直接下载/预览（前端代理到后端 /materials/{uid}/download）
  window.open(`/api/materials/${row.uid}/download`, "_blank");
};

const activeTab = ref("inspections");

// ── 分块编辑 ──────────────────────────────────────────────
type FieldType = "text" | "number" | "select" | "multiselect" | "bool" | "tristate";
interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  options?: readonly string[];
  span?: number;
}

interface SectionDef {
  title: string;
  /** 分块字段（extra 分块走 JSON 编辑器，fields 为空） */
  fields: FieldDef[];
  /** 是否显示该分块编辑按钮（按企业数据动态判断） */
  enabled: (e: Enterprise) => boolean;
}

const SECTION_DEFS: Record<string, SectionDef> = {
  base: {
    title: "工商与人员",
    enabled: () => true,
    fields: [
      { key: "legal_person", label: "法定代表人", type: "text" },
      { key: "legal_person_phone", label: "法人电话", type: "text" },
      { key: "person_in_charge", label: "企业负责人", type: "text" },
      { key: "person_in_charge_phone", label: "负责人电话", type: "text" },
      { key: "safety_officer", label: "企业安全员", type: "text" },
      { key: "safety_officer_phone", label: "安全员电话", type: "text" },
      { key: "address", label: "详细地址", type: "text", span: 24 }
    ]
  },
  scale: {
    title: "经营规模",
    enabled: () => true,
    fields: [
      { key: "employee_count", label: "职工人数", type: "number" },
      { key: "annual_output_value", label: "年产值（万元）", type: "number" },
      { key: "fire_level", label: "消防重点单位", type: "select", options: fireLevelOptions }
    ]
  },
  risk: {
    title: "安全风险画像",
    enabled: () => true,
    fields: [
      {
        key: "confined_space",
        label: "有限空间",
        type: "multiselect",
        options: confinedSpaceOptions,
        span: 24
      },
      ...safetyFlags.map(f => ({ key: f.key, label: f.label, type: "bool" as const }))
    ]
  },
  confined: {
    title: "有限空间台账",
    enabled: e =>
      (e.confined_space || []).length > 0 ||
      !!e.confined_space_count ||
      !!e.confined_space_type ||
      !!e.confined_space_hazards,
    fields: [
      { key: "confined_space_count", label: "空间数量", type: "text" },
      { key: "confined_space_type", label: "空间类型（详述）", type: "text" },
      { key: "confined_space_workers", label: "作业人数", type: "text" },
      { key: "confined_space_op_subject", label: "作业主体", type: "text" },
      { key: "confined_space_op_cycle", label: "作业周期", type: "text" },
      { key: "confined_space_fixed_workers", label: "作业人员是否固定", type: "text" },
      { key: "confined_space_hazards", label: "主要危险因素", type: "text", span: 24 }
    ]
  },
  hazchem: {
    title: "危化品信息",
    enabled: e =>
      e.has_hazardous_chemicals ||
      !!e.hazchem_names ||
      (profile.value?.hazardous_chemicals?.length ?? 0) > 0,
    fields: [
      { key: "hazchem_names", label: "危化品名称", type: "text", span: 24 },
      { key: "hazchem_op_subject", label: "作业主体", type: "text" },
      { key: "hazchem_major_hazard", label: "重大危险源", type: "text" },
      { key: "hazchem_state", label: "危化品状态", type: "text" },
      { key: "hazchem_op_cycle", label: "作业周期", type: "text" },
      { key: "hazchem_usage", label: "用途", type: "text" },
      { key: "hazchem_annual_usage", label: "年度使用量", type: "text" },
      { key: "hazchem_storage", label: "存储方式", type: "text" },
      { key: "hazchem_reserve", label: "储量", type: "text" },
      { key: "hazchem_fixed_workers", label: "作业人员是否固定", type: "text" }
    ]
  },
  venue: {
    title: "小场所台账",
    enabled: e => (e.enterprise_category || []).includes("小场所"),
    fields: [
      { key: "venue_status", label: "场所状态", type: "text" },
      { key: "staff_count", label: "从业人数", type: "text" },
      { key: "venue_area", label: "场所面积", type: "text" },
      { key: "capacity", label: "承载人数", type: "text" },
      { key: "is_multistory", label: "占用多层", type: "tristate" },
      { key: "has_business_license", label: "有无营业执照", type: "tristate" },
      { key: "venue_category_major", label: "场所大类", type: "text" },
      { key: "venue_category_minor", label: "场所小类", type: "text" },
      { key: "is_multi_license", label: "是否一店多证", type: "tristate" },
      { key: "venue_risk_level", label: "风险等级", type: "text" },
      { key: "regulatory_dept", label: "专业监管部门", type: "text", span: 24 },
      { key: "territorial_dept", label: "属地管理部门", type: "text", span: 24 },
      { key: "venue_safety_inspector", label: "安全巡查员", type: "text" },
      { key: "venue_fire_instructor", label: "消防指导员", type: "text" },
      { key: "involves_evacuation", label: "疏散安全", type: "bool" },
      { key: "involves_fire_safety", label: "消防安全", type: "bool" },
      { key: "involves_electrical", label: "用电安全", type: "bool" },
      { key: "involves_lpg", label: "瓶装液化气", type: "bool" },
      { key: "involves_pipe_gas", label: "管道天然气", type: "bool" },
      { key: "involves_other_fuel", label: "其他燃料", type: "bool" },
      { key: "involves_decoration_work", label: "装饰装修及危险作业", type: "bool" },
      { key: "involves_three_in_one", label: "三合一场所", type: "bool" },
      { key: "involves_flammable_storage", label: "储存易燃易爆物品", type: "bool" }
    ]
  },
  extra: {
    title: "自定义信息",
    enabled: e => extraEntries(e).length > 0,
    fields: []
  }
};

const editVisible = ref(false);
const editSectionKey = ref<string>("");
const editFields = ref<FieldDef[]>([]);
const editForm = reactive<Record<string, any>>({});
const editExtraText = ref("");
const editLoading = ref(false);

const editTitle = computed(() => SECTION_DEFS[editSectionKey.value]?.title ?? "");
const isExtraEdit = computed(() => editSectionKey.value === "extra");
const sectionEnabled = (key: string) =>
  ent.value ? SECTION_DEFS[key].enabled(ent.value) : false;

function openSectionEdit(key: string) {
  const sec = SECTION_DEFS[key];
  const e = ent.value;
  if (!sec || !e) return;
  editSectionKey.value = key;
  editFields.value = sec.fields;
  if (key === "extra") {
    editExtraText.value = JSON.stringify(e.extra ?? {}, null, 2);
  } else {
    for (const k of Object.keys(editForm)) delete editForm[k];
    for (const fd of sec.fields) {
      const raw = (e as any)[fd.key];
      let v: any;
      if (fd.type === "number") v = raw ?? null;
      else if (fd.type === "multiselect") v = Array.isArray(raw) ? [...raw] : [];
      else if (fd.type === "bool") v = !!raw;
      else if (fd.type === "tristate") v = raw === true || raw === false ? raw : "";
      else v = raw ?? "";
      editForm[fd.key] = v;
    }
  }
  editVisible.value = true;
}

function buildSectionPayload(): Partial<Enterprise> {
  const p: Record<string, any> = {};
  for (const fd of editFields.value) {
    let v = editForm[fd.key];
    if (fd.type === "number") v = v ?? null;
    else if (fd.type === "multiselect") v = Array.isArray(v) ? v : [];
    else if (fd.type === "tristate") v = v === "" || v === undefined ? null : v;
    else if (fd.type === "text") v = v ?? "";
    p[fd.key] = v;
  }
  return p as Partial<Enterprise>;
}

async function submitSection() {
  const e = ent.value;
  if (!e) return;
  let payload: Partial<Enterprise>;
  if (isExtraEdit.value) {
    try {
      payload = { extra: JSON.parse(editExtraText.value || "{}") };
    } catch {
      ElMessage.error("自定义字段不是合法的 JSON，请检查格式");
      return;
    }
  } else {
    payload = buildSectionPayload();
  }
  try {
    await ElMessageBox.confirm(
      `确认保存「${editTitle.value}」的修改？`,
      "确认修改",
      { type: "warning", confirmButtonText: "确认保存", cancelButtonText: "取消" }
    );
  } catch {
    return; // 用户取消
  }
  editLoading.value = true;
  try {
    await updateEnterprise(e.id, payload);
    ElMessage.success("已保存");
    editVisible.value = false;
    await load();
  } catch (err: any) {
    const msg =
      err?.response?.data?.detail || err?.response?.data?.message || err?.message || "保存失败";
    ElMessage.error(msg);
  } finally {
    editLoading.value = false;
  }
}
</script>

<template>
  <div v-loading="loading" class="profile-page">
    <!-- ── 未选企业：企业选择器 ───────────────────────── -->
    <template v-if="!hasId">
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="section-title">企业档案 · 请选择企业</span>
        </template>
        <el-input
          v-model="pickerKeyword"
          :prefix-icon="Search"
          placeholder="输入企业名称 / 统一社会信用代码后回车查询"
          clearable
          style="max-width: 420px"
          @keyup.enter="searchEnterprises"
          @clear="searchEnterprises"
        />
        <el-table
          v-loading="pickerLoading"
          :data="pickerList"
          border
          stripe
          size="small"
          style="width: 100%; margin-top: 12px"
        >
          <el-table-column prop="name" label="企业名称" min-width="220" show-overflow-tooltip />
          <el-table-column prop="code" label="统一社会信用代码" width="190" />
          <el-table-column prop="industry" label="行业" width="110" />
          <el-table-column prop="region" label="区域" width="140" />
          <el-table-column label="操作" width="100" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="selectEnterprise(row)">
                查看档案
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <template v-else-if="ent">
      <!-- ── 顶部概览 ─────────────────────────────────── -->
      <el-card shadow="never" class="head-card">
        <div class="head-top">
          <el-button link :icon="ArrowLeft" @click="goBack">返回</el-button>
          <h1 class="ent-name">{{ ent.name }}</h1>
        </div>

        <div class="tag-row">
          <el-tag
            v-for="c in ent.enterprise_category"
            :key="c"
            :type="categoryTagType(c)"
            size="small"
          >
            {{ c }}
          </el-tag>
          <el-tag v-if="ent.asset_attribute" type="info" size="small" effect="plain">
            资产：{{ ent.asset_attribute }}
          </el-tag>
          <el-tag v-if="ent.industry" type="info" size="small" effect="plain">
            行业：{{ ent.industry }}
          </el-tag>
          <el-tag v-if="ent.region" type="info" size="small" effect="plain">
            区域：{{ ent.region }}
          </el-tag>
          <el-tag v-if="ent.fire_level" type="warning" size="small" effect="plain">
            消防：{{ ent.fire_level }}
          </el-tag>
          <el-tag v-if="ent.enterprise_scale" type="info" size="small" effect="plain">
            规模：{{ ent.enterprise_scale }}
          </el-tag>
        </div>

        <div class="code-row">
          统一社会信用代码：<span class="mono">{{ dash(ent.code) }}</span>
        </div>

        <el-alert
          v-if="ent.out_of_park"
          class="park-alert"
          type="warning"
          :closable="false"
          show-icon
          title="非新能源汽车产业园区企业（待人工复核）"
          :description="ent.out_of_park_note || ''"
        />
      </el-card>

      <!-- ── ① 工商与人员 ─────────────────────────────── -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">工商与人员</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('base')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="法定代表人">{{ dash(ent.legal_person) }}</el-descriptions-item>
          <el-descriptions-item label="企业负责人">{{ dash(ent.person_in_charge) }}</el-descriptions-item>
          <el-descriptions-item label="企业安全员">{{ dash(ent.safety_officer) }}</el-descriptions-item>
          <el-descriptions-item label="法人电话">{{ dash(ent.legal_person_phone) }}</el-descriptions-item>
          <el-descriptions-item label="负责人电话">{{ dash(ent.person_in_charge_phone) }}</el-descriptions-item>
          <el-descriptions-item label="安全员电话">{{ dash(ent.safety_officer_phone) }}</el-descriptions-item>
          <el-descriptions-item label="详细地址" :span="3">{{ dash(ent.address) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- ── ② 经营规模 ──────────────────────────────── -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">经营规模</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('scale')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="职工人数">{{ dash(ent.employee_count) }}</el-descriptions-item>
          <el-descriptions-item label="年产值（万元）">{{ dash(ent.annual_output_value) }}</el-descriptions-item>
          <el-descriptions-item label="消防重点单位">{{ dash(ent.fire_level) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- ── ③ 安全风险画像 ─────────────────────────── -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">安全风险画像</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('risk')">
              编辑
            </el-button>
          </div>
        </template>
        <div v-if="riskFlags.length" class="tag-row">
          <el-tag v-for="f in riskFlags" :key="f" type="danger" size="small" effect="light">
            {{ f }}
          </el-tag>
        </div>
        <el-empty v-else description="无风险标记" :image-size="60" />
      </el-card>

      <!-- ── ④ 有限空间台账 ──────────────────────────── -->
      <el-card v-if="sectionEnabled('confined')" shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">有限空间台账</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('confined')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="空间类型">
            <el-tag
              v-for="s in ent.confined_space"
              :key="s"
              type="info"
              size="small"
              style="margin-right: 4px"
            >
              {{ s }}
            </el-tag>
            <span v-if="!(ent.confined_space || []).length">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="空间数量">{{ dash(ent.confined_space_count) }}</el-descriptions-item>
          <el-descriptions-item label="作业人数">{{ dash(ent.confined_space_workers) }}</el-descriptions-item>
          <el-descriptions-item label="空间类型（详述）">{{ dash(ent.confined_space_type) }}</el-descriptions-item>
          <el-descriptions-item label="作业主体">{{ dash(ent.confined_space_op_subject) }}</el-descriptions-item>
          <el-descriptions-item label="作业周期">{{ dash(ent.confined_space_op_cycle) }}</el-descriptions-item>
          <el-descriptions-item label="作业人员是否固定">{{ dash(ent.confined_space_fixed_workers) }}</el-descriptions-item>
          <el-descriptions-item label="主要危险因素" :span="2">{{ dash(ent.confined_space_hazards) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- ── ⑤ 危化品信息 ───────────────────────────── -->
      <el-card v-if="sectionEnabled('hazchem')" shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">危化品信息</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('hazchem')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="危化品名称" :span="2">{{ dash(ent.hazchem_names) }}</el-descriptions-item>
          <el-descriptions-item label="作业主体">{{ dash(ent.hazchem_op_subject) }}</el-descriptions-item>
          <el-descriptions-item label="重大危险源">{{ dash(ent.hazchem_major_hazard) }}</el-descriptions-item>
          <el-descriptions-item label="危化品状态">{{ dash(ent.hazchem_state) }}</el-descriptions-item>
          <el-descriptions-item label="作业周期">{{ dash(ent.hazchem_op_cycle) }}</el-descriptions-item>
          <el-descriptions-item label="用途">{{ dash(ent.hazchem_usage) }}</el-descriptions-item>
          <el-descriptions-item label="年度使用量">{{ dash(ent.hazchem_annual_usage) }}</el-descriptions-item>
          <el-descriptions-item label="存储方式">{{ dash(ent.hazchem_storage) }}</el-descriptions-item>
          <el-descriptions-item label="储量">{{ dash(ent.hazchem_reserve) }}</el-descriptions-item>
          <el-descriptions-item label="作业人员是否固定">{{ dash(ent.hazchem_fixed_workers) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- ── ⑥ 小场所台账 ───────────────────────────── -->
      <el-card v-if="sectionEnabled('venue')" shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">小场所台账</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('venue')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="场所状态">{{ dash(ent.venue_status) }}</el-descriptions-item>
          <el-descriptions-item label="从业人数">{{ dash(ent.staff_count) }}</el-descriptions-item>
          <el-descriptions-item label="场所面积">{{ dash(ent.venue_area) }}</el-descriptions-item>
          <el-descriptions-item label="承载人数">{{ dash(ent.capacity) }}</el-descriptions-item>
          <el-descriptions-item label="占用多层">{{ boolText(ent.is_multistory) }}</el-descriptions-item>
          <el-descriptions-item label="有无营业执照">{{ boolText(ent.has_business_license) }}</el-descriptions-item>
          <el-descriptions-item label="场所大类">{{ dash(ent.venue_category_major) }}</el-descriptions-item>
          <el-descriptions-item label="场所小类">{{ dash(ent.venue_category_minor) }}</el-descriptions-item>
          <el-descriptions-item label="是否一店多证">{{ boolText(ent.is_multi_license) }}</el-descriptions-item>
          <el-descriptions-item label="风险等级">{{ dash(ent.venue_risk_level) }}</el-descriptions-item>
          <el-descriptions-item label="专业监管部门" :span="2">{{ dash(ent.regulatory_dept) }}</el-descriptions-item>
          <el-descriptions-item label="属地管理部门" :span="3">{{ dash(ent.territorial_dept) }}</el-descriptions-item>
          <el-descriptions-item label="安全巡查员" :span="2">{{ dash(ent.venue_safety_inspector) }}</el-descriptions-item>
          <el-descriptions-item label="消防指导员">{{ dash(ent.venue_fire_instructor) }}</el-descriptions-item>
        </el-descriptions>

        <div class="sub-title">涉及安全事项</div>
        <div class="tag-row">
          <el-tag v-if="ent.involves_evacuation" type="danger" size="small" effect="plain">疏散安全</el-tag>
          <el-tag v-if="ent.involves_fire_safety" type="danger" size="small" effect="plain">消防安全</el-tag>
          <el-tag v-if="ent.involves_electrical" type="danger" size="small" effect="plain">用电安全</el-tag>
          <el-tag v-if="ent.involves_lpg" type="warning" size="small" effect="plain">瓶装液化气</el-tag>
          <el-tag v-if="ent.involves_pipe_gas" type="warning" size="small" effect="plain">管道天然气</el-tag>
          <el-tag v-if="ent.involves_other_fuel" type="warning" size="small" effect="plain">其他燃料</el-tag>
          <el-tag v-if="ent.involves_decoration_work" type="warning" size="small" effect="plain">装饰装修及危险作业</el-tag>
          <el-tag v-if="ent.involves_three_in_one" type="danger" size="small" effect="plain">三合一场所</el-tag>
          <el-tag v-if="ent.involves_flammable_storage" type="danger" size="small" effect="plain">储存易燃易爆物品</el-tag>
          <span
            v-if="!ent.involves_evacuation && !ent.involves_fire_safety && !ent.involves_electrical && !ent.involves_lpg && !ent.involves_pipe_gas && !ent.involves_other_fuel && !ent.involves_decoration_work && !ent.involves_three_in_one && !ent.involves_flammable_storage"
            class="muted"
          >—</span>
        </div>
      </el-card>

      <!-- ── ⑦ 自定义信息 ───────────────────────────── -->
      <el-card v-if="sectionEnabled('extra')" shadow="never" class="section-card">
        <template #header>
          <div class="card-head">
            <span class="section-title">自定义信息</span>
            <el-button link type="primary" size="small" @click="openSectionEdit('extra')">
              编辑
            </el-button>
          </div>
        </template>
        <el-descriptions :column="2" border>
          <el-descriptions-item
            v-for="([k, v]) in extraList"
            :key="k"
            :label="k"
          >
            {{ v }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- ── 关联记录 ───────────────────────────────── -->
      <el-card shadow="never" class="section-card">
        <template #header><span class="section-title">关联记录</span></template>
        <el-tabs v-model="activeTab">
          <!-- 巡查整改 -->
          <el-tab-pane :label="`巡查整改（${inspections.length}）`" name="inspections">
            <el-table :data="inspections" border stripe size="small" style="width: 100%">
              <el-table-column prop="inspection_batch" label="巡查批次" width="150" />
              <el-table-column prop="inspection_date" label="巡查日期" width="115" />
              <el-table-column prop="inspector_name" label="检查人" width="90" />
              <el-table-column label="隐患等级" width="80" align="center">
                <template #default="{ row }">
                  <el-tag :type="levelTagType(row.hazard_level)" size="small">
                    {{ levelText(row.hazard_level) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="hazard_category" label="隐患类别" width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.hazard_category) }}</template>
              </el-table-column>
              <el-table-column prop="hazard_description" label="隐患描述" min-width="200" show-overflow-tooltip />
              <el-table-column prop="location" label="隐患地点" width="130" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.location) }}</template>
              </el-table-column>
              <el-table-column prop="notice_number" label="通知单号" width="130" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.notice_number) }}</template>
              </el-table-column>
              <el-table-column label="整改状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag
                    v-if="isInspOverdue(row)"
                    type="danger"
                    size="small"
                    effect="plain"
                  >
                    超期
                  </el-tag>
                  <el-tag v-else :type="inspStatusTagType(row.status)" size="small" effect="plain">
                    {{ inspStatusText(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="rect_responsible_name" label="整改责任人" width="100">
                <template #default="{ row }">{{ dash(row.rect_responsible_name) }}</template>
              </el-table-column>
              <el-table-column prop="rect_deadline" label="整改期限" width="115" />
              <el-table-column prop="rect_completed_at" label="完成时间" width="115">
                <template #default="{ row }">{{ dash(fmtDate(row.rect_completed_at)) }}</template>
              </el-table-column>
              <el-table-column prop="review_date" label="复查日期" width="115">
                <template #default="{ row }">{{ dash(fmtDate(row.review_date)) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!inspections.length" description="暂无巡查记录" :image-size="60" />
          </el-tab-pane>

          <!-- 厂中厂 -->
          <el-tab-pane :label="`厂中厂（${factoryRelations.length}）`" name="factory">
            <el-table :data="factoryRelations" border stripe size="small" style="width: 100%">
              <el-table-column prop="role" label="本企业角色" width="90" align="center" />
              <el-table-column prop="site_category" label="场所类别" width="100">
                <template #default="{ row }">{{ dash(row.site_category) }}</template>
              </el-table-column>
              <el-table-column prop="inspection_batch" label="整治批次" width="140" />
              <el-table-column prop="inspection_date" label="排查日期" width="115" />
              <el-table-column prop="counterParty" label="相对方" min-width="160" show-overflow-tooltip />
              <el-table-column prop="area" label="面积" width="100" />
              <el-table-column prop="contactPerson" label="联系人" width="90" />
              <el-table-column prop="contactPhone" label="联系电话" width="120" />
              <el-table-column prop="agreement_signed" label="协议签订" width="100" align="center">
                <template #default="{ row }">{{ dash(row.agreement_signed) }}</template>
              </el-table-column>
              <el-table-column prop="hazard" label="隐患描述" min-width="180" show-overflow-tooltip />
              <el-table-column prop="rectStatus" label="整改情况" width="130" show-overflow-tooltip />
              <el-table-column prop="rect_supervisor" label="整改监管人" width="100">
                <template #default="{ row }">{{ dash(row.rect_supervisor) }}</template>
              </el-table-column>
              <el-table-column prop="address" label="地址" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.address) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!factoryRelations.length" description="暂无厂中厂关系" :image-size="60" />
          </el-tab-pane>

          <!-- 危化品明细 -->
          <el-tab-pane :label="`危化品明细（${chemicals.length}）`" name="chemicals">
            <el-table :data="chemicals" border stripe size="small" style="width: 100%">
              <el-table-column prop="hazchem_name" label="危化品名称" min-width="140" show-overflow-tooltip />
              <el-table-column prop="hazchem_state" label="状态" width="80" align="center">
                <template #default="{ row }">{{ dash(row.hazchem_state) }}</template>
              </el-table-column>
              <el-table-column prop="reserve" label="储量" width="90" align="center">
                <template #default="{ row }">{{ dash(row.reserve) }}</template>
              </el-table-column>
              <el-table-column prop="max_reserve" label="最大储量" width="100" align="center">
                <template #default="{ row }">{{ dash(row.max_reserve) }}</template>
              </el-table-column>
              <el-table-column prop="annual_usage" label="年使用量" width="100" align="center">
                <template #default="{ row }">{{ dash(row.annual_usage) }}</template>
              </el-table-column>
              <el-table-column prop="usage" label="用途" width="100" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.usage) }}</template>
              </el-table-column>
              <el-table-column prop="storage_method" label="存储方式" width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.storage_method) }}</template>
              </el-table-column>
              <el-table-column prop="storage_location" label="存储位置" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.storage_location) }}</template>
              </el-table-column>
              <el-table-column label="重大危险源" width="120" align="center">
                <template #default="{ row }">
                  <el-tag :type="majorHazardTagType(row.major_hazard)" size="small" effect="plain">
                    {{ dash(row.major_hazard) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">{{ dash(row.remark) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!chemicals.length" description="暂无危化品明细" :image-size="60" />
          </el-tab-pane>

          <!-- 相关材料 -->
          <el-tab-pane :label="`相关材料（${materials.length}）`" name="materials">
            <el-table :data="materials" border stripe size="small" style="width: 100%">
              <el-table-column prop="filename" label="文件名" min-width="260" show-overflow-tooltip />
              <el-table-column prop="source_format" label="格式" width="90" align="center">
                <template #default="{ row }">{{ dash(row.source_format) }}</template>
              </el-table-column>
              <el-table-column prop="page_count" label="页数" width="70" align="center">
                <template #default="{ row }">{{ row.page_count ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="状态" width="100" align="center">
                <template #default="{ row }">
                  <el-tag :type="matStatusTagType(row.status)" size="small" effect="plain">
                    {{ matStatusText(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="上传时间" width="120">
                <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="120" align="center">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openMaterial(row)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!materials.length" description="暂无定向到该企业的安全材料" :image-size="60" />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="企业不存在" />

    <!-- ── 分块编辑弹窗 ─────────────────────────────── -->
    <el-dialog
      v-model="editVisible"
      :title="`编辑 · ${editTitle}`"
      width="720px"
      destroy-on-close
    >
      <el-form v-if="isExtraEdit" label-width="90px">
        <el-form-item label="自定义字段">
          <el-input
            v-model="editExtraText"
            type="textarea"
            :rows="10"
            placeholder='JSON 格式，例如 {"联系电话": "0515-1234567", "来源": ["巡查", "自查"]}'
          />
          <div class="hint">
            以键值对 JSON 维护；数组值表示多值字段。保存前请确保为合法 JSON。
          </div>
        </el-form-item>
      </el-form>

      <el-form v-else label-width="120px">
        <el-row :gutter="16">
          <el-col v-for="f in editFields" :key="f.key" :span="f.span ?? 12">
            <el-form-item :label="f.label">
              <el-input
                v-if="f.type === 'text'"
                v-model="editForm[f.key]"
                clearable
                placeholder="请输入"
              />
              <el-input-number
                v-else-if="f.type === 'number'"
                v-model="editForm[f.key]"
                :min="0"
                :precision="0"
                controls-position="right"
                style="width: 100%"
              />
              <el-select
                v-else-if="f.type === 'select'"
                v-model="editForm[f.key]"
                clearable
                style="width: 100%"
                placeholder="请选择"
              >
                <el-option v-for="o in f.options" :key="o" :label="o" :value="o" />
              </el-select>
              <el-select
                v-else-if="f.type === 'multiselect'"
                v-model="editForm[f.key]"
                multiple
                collapse-tags
                collapse-tags-tooltip
                clearable
                style="width: 100%"
                placeholder="请选择（可多选）"
              >
                <el-option v-for="o in f.options" :key="o" :label="o" :value="o" />
              </el-select>
              <el-select
                v-else-if="f.type === 'tristate'"
                v-model="editForm[f.key]"
                clearable
                style="width: 100%"
                placeholder="未填报"
              >
                <el-option label="是" :value="true" />
                <el-option label="否" :value="false" />
              </el-select>
              <el-switch v-else-if="f.type === 'bool'" v-model="editForm[f.key]" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editLoading" @click="submitSection">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.profile-page {
  padding: 16px;
}

.head-card {
  margin-bottom: 12px;
}

.head-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ent-name {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.code-row {
  margin-top: 12px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  color: var(--el-text-color-primary);
}

.park-alert {
  margin-top: 12px;
}

.section-card {
  margin-bottom: 12px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sub-title {
  margin: 14px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.muted {
  color: var(--el-text-color-placeholder);
}

.hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  line-height: 1.5;
}
</style>
