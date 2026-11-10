<!--
  企业新建/编辑弹窗 —— 企业主表 / 有限空间专页 / 危化品专页共用。

  字段可见性按当前页表格列裁剪（pageColumns）：
  - 新建模式：基础/规模/安全/自定义字段全量显示；详情字段按当前页是否含该列决定。
  - 编辑模式：仅显示当前页表格中出现的字段，未显示字段不提交
    （后端 update_enterprise 用 model_fields_set 做 PATCH 语义，未提交列保持原值）。
-->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEdit ? '编辑企业' : '新建企业'"
    width="880px"
    destroy-on-close
    @update:model-value="v => emit('update:modelValue', v)"
    @open="onOpen"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
      <el-row :gutter="16">
        <!-- ── 基础信息 ── -->
        <el-col v-if="showBaseGroup" :span="24">
          <div class="form-section-title">基础信息</div>
        </el-col>
        <el-col v-if="fieldShown('name')" :span="12">
          <el-form-item label="企业名称" prop="name">
            <el-input v-model="form.name" placeholder="请输入企业名称" />
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('code')" :span="12">
          <el-form-item label="信用代码" prop="code">
            <el-input
              v-model="form.code"
              placeholder="18位统一社会信用代码"
              maxlength="18"
            />
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('category')" :span="12">
          <el-form-item label="企业类型">
            <el-select
              v-model="form.enterprise_category"
              placeholder="请选择（可多选）"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="opt in categoryOptions"
                :key="opt"
                :label="opt"
                :value="opt"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('asset')" :span="12">
          <el-form-item label="资产属性">
            <el-select
              v-model="form.asset_attribute"
              placeholder="请选择"
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="opt in assetOptions"
                :key="opt"
                :label="opt"
                :value="opt"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('industry')" :span="12">
          <el-form-item label="行业" prop="industry">
            <el-select
              v-model="form.industry"
              placeholder="请选择"
              style="width: 100%"
              filterable
              allow-create
              default-first-option
            >
              <el-option
                v-for="item in industryOptions"
                :key="item"
                :label="item"
                :value="item"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('region')" :span="12">
          <el-form-item label="区域" prop="region">
            <el-select
              v-model="form.region"
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
        </el-col>
        <el-row v-if="fieldShown('legal_person')" :gutter="8">
          <el-col :span="12">
            <el-form-item label="法人" label-width="70px">
              <el-input v-model="form.legal_person" placeholder="法人姓名" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="法人电话" label-width="70px">
              <el-input
                v-model="form.legal_person_phone"
                placeholder="法人电话"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row v-if="fieldShown('person_in_charge')" :gutter="8">
          <el-col :span="12">
            <el-form-item label="企业负责人" label-width="92px">
              <el-input
                v-model="form.person_in_charge"
                placeholder="负责人姓名"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="负责人电话" label-width="92px">
              <el-input
                v-model="form.person_in_charge_phone"
                placeholder="负责人电话"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row v-if="fieldShown('safety_officer')" :gutter="8">
          <el-col :span="12">
            <el-form-item label="企业安全员" label-width="92px">
              <el-input
                v-model="form.safety_officer"
                placeholder="安全员姓名"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="安全员电话" label-width="92px">
              <el-input
                v-model="form.safety_officer_phone"
                placeholder="安全员电话"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-col v-if="fieldShown('address')" :span="24">
          <el-form-item label="地址">
            <el-input v-model="form.address" placeholder="请输入地址" />
          </el-form-item>
        </el-col>

        <!-- ── 规模信息 ── -->
        <el-col v-if="showScaleGroup" :span="24">
          <div class="form-section-title">规模信息</div>
        </el-col>
        <el-col v-if="fieldShown('employee_count')" :span="8">
          <el-form-item label="职工人数">
            <el-input-number
              v-model="form.employee_count"
              :min="0"
              :precision="0"
              controls-position="right"
              placeholder="人数"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('annual_output_value')" :span="8">
          <el-form-item label="年产值(万元)">
            <el-input-number
              v-model="form.annual_output_value"
              :min="0"
              :precision="0"
              controls-position="right"
              placeholder="万元"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>

        <!-- ── 安全信息 ── -->
        <el-col v-if="showSafetyGroup" :span="24">
          <div class="form-section-title">安全信息</div>
        </el-col>
        <el-col v-if="fieldShown('fire_level')" :span="8">
          <el-form-item label="消防重点单位">
            <el-select
              v-model="form.fire_level"
              placeholder="请选择"
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="opt in fireLevelOptions"
                :key="opt"
                :label="opt"
                :value="opt"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('confined_space')" :span="8">
          <el-form-item label="有限空间">
            <el-select
              v-model="form.confined_space"
              placeholder="请选择（可多选）"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="opt in confinedSpaceOptions"
                :key="opt"
                :label="opt"
                :value="opt"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col v-if="fieldShown('flags')" :span="24">
          <el-form-item label="风险点">
            <div class="safety-grid">
              <div v-for="f in safetyFlags" :key="f.key" class="safety-item">
                <span>{{ f.label }}</span>
                <el-switch v-model="form[f.key]" />
              </div>
            </div>
          </el-form-item>
        </el-col>

        <!-- ── 有限空间详情（仅有限空间专页显示） ── -->
        <template v-if="showConfinedSpaceDetail">
          <el-col :span="24">
            <div class="form-section-title">有限空间详情</div>
          </el-col>
          <el-col :span="8">
            <el-form-item label="空间数量">
              <el-input
                v-model="form.confined_space_count"
                placeholder="空间数量"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="空间类型">
              <el-input
                v-model="form.confined_space_type"
                placeholder="空间类型"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="作业人数">
              <el-input
                v-model="form.confined_space_workers"
                placeholder="作业人数"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="主要危险因素">
              <el-input
                v-model="form.confined_space_hazards"
                placeholder="主要危险因素"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="空间作业主体">
              <el-input
                v-model="form.confined_space_op_subject"
                placeholder="空间作业主体"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="空间作业周期">
              <el-input
                v-model="form.confined_space_op_cycle"
                placeholder="空间作业周期"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="作业人员固定">
              <el-input
                v-model="form.confined_space_fixed_workers"
                placeholder="作业人员固定"
              />
            </el-form-item>
          </el-col>
        </template>

        <!-- ── 危化品详情（仅危化品专页显示） ── -->
        <template v-if="showHazchemDetail">
          <el-col :span="24">
            <div class="form-section-title">危化品详情</div>
          </el-col>
          <el-col :span="8">
            <el-form-item label="危化品名称">
              <el-input v-model="form.hazchem_names" placeholder="危化品名称" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="作业主体">
              <el-input
                v-model="form.hazchem_op_subject"
                placeholder="作业主体"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="重大危险源">
              <el-input
                v-model="form.hazchem_major_hazard"
                placeholder="重大危险源"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="危化品状态">
              <el-input v-model="form.hazchem_state" placeholder="危化品状态" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="作业周期">
              <el-input
                v-model="form.hazchem_op_cycle"
                placeholder="作业周期"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="用途">
              <el-input v-model="form.hazchem_usage" placeholder="用途" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="年度使用量">
              <el-input
                v-model="form.hazchem_annual_usage"
                placeholder="年度使用量"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="存储方式">
              <el-input v-model="form.hazchem_storage" placeholder="存储方式" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="储量">
              <el-input v-model="form.hazchem_reserve" placeholder="储量" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="固定作业人员">
              <el-input
                v-model="form.hazchem_fixed_workers"
                placeholder="固定作业人员"
              />
            </el-form-item>
          </el-col>
        </template>

        <!-- ── 其他自定义字段（长尾，jsonb 动态键值对） ── -->
        <el-col v-if="showExtraGroup" :span="24">
          <div class="form-section-title">其他自定义字段</div>
        </el-col>
        <el-col v-if="fieldShown('extra')" :span="24">
          <el-form-item label="自定义字段">
            <div class="w-full">
              <div
                v-for="(row, index) in extraRows"
                :key="index"
                class="flex items-center mb-2"
              >
                <el-select v-model="row.type" class="mr-2" style="width: 84px">
                  <el-option label="文本" value="text" />
                  <el-option label="多值" value="multi" />
                </el-select>
                <!-- 字段名：历史字段下拉 + 可直接输入新字段（保存后沉淀进下拉） -->
                <el-select
                  v-model="row.key"
                  filterable
                  allow-create
                  default-first-option
                  :reserve-keyword="false"
                  placeholder="选择或输入字段名"
                  style="width: 180px"
                >
                  <el-option
                    v-for="k in allExtraKeys(row)"
                    :key="k"
                    :label="k"
                    :value="k"
                  />
                </el-select>
                <span class="mx-2 text-gray-400">=</span>
                <!-- 文本单值 -->
                <el-input
                  v-if="row.type === 'text'"
                  v-model="row.textValue"
                  placeholder="字段值"
                  class="flex-1"
                />
                <!-- 多值：历史值下拉 + 可输入新值，回车生成标签 -->
                <el-select
                  v-else
                  v-model="row.multiValue"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  :reserve-keyword="false"
                  placeholder="点此选择，或输入后回车添加（可多选）"
                  class="flex-1"
                >
                  <el-option
                    v-for="opt in multiCandidates(row)"
                    :key="opt"
                    :label="opt"
                    :value="opt"
                  />
                </el-select>
                <el-button
                  link
                  type="danger"
                  class="ml-2"
                  @click="removeExtraRow(index)"
                >
                  删除
                </el-button>
              </div>
              <el-button link type="primary" @click="addExtraRow">
                + 添加字段
              </el-button>
              <span class="ml-2 text-xs text-gray-400">
                字段名可从已有字段中选择或直接输入新字段；「多值」可填多个值。新填的字段名和选项保存后会自动进入下拉，供后续所有企业选择
              </span>
            </div>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="submitLoading"
        @click="handleSubmit(formRef)"
      >
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  createEnterprise,
  updateEnterprise,
  getEnterpriseExtraOptions
} from "@/api/enterprises";
import type {
  Enterprise,
  EnterpriseCategory,
  FireLevel,
  AssetAttribute,
  ConfinedSpace
} from "@/api/enterprises";
import {
  industryOptions,
  categoryOptions,
  assetOptions,
  fireLevelOptions,
  confinedSpaceOptions,
  safetyFlags
} from "./table.config";

const props = defineProps<{
  modelValue: boolean;
  /** 编辑的企业行；null = 新建 */
  row: Enterprise | null;
  /** 当前页表格列 key（字段裁剪依据） */
  pageColumns: string[];
  /** 区域动态选项 */
  regionOptions: string[];
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "saved"): void;
}>();

const isEdit = computed(() => !!props.row);

/** 表单态（extra 单独用行编辑器维护，提交时组装） */
interface EnterpriseForm {
  id: number;
  name: string;
  code: string;
  industry: string;
  region: string;
  address: string;
  legal_person: string;
  person_in_charge: string;
  safety_officer: string;
  legal_person_phone: string;
  person_in_charge_phone: string;
  safety_officer_phone: string;
  enterprise_category: EnterpriseCategory[];
  asset_attribute: AssetAttribute;
  employee_count: number | null;
  annual_output_value: number | null;
  fire_level: FireLevel;
  confined_space: ConfinedSpace[];
  has_natural_gas: boolean;
  is_injection_molding: boolean;
  has_dormitory: boolean;
  has_hazardous_chemicals: boolean;
  has_hazardous_waste: boolean;
  involves_dust_explosion: boolean;
  is_new_energy_welding: boolean;
  has_large_storage_tank: boolean;
  confined_space_count: string;
  confined_space_type: string;
  confined_space_workers: string;
  confined_space_hazards: string;
  confined_space_op_subject: string;
  confined_space_op_cycle: string;
  confined_space_fixed_workers: string;
  hazchem_names: string;
  hazchem_op_subject: string;
  hazchem_major_hazard: string;
  hazchem_state: string;
  hazchem_op_cycle: string;
  hazchem_usage: string;
  hazchem_annual_usage: string;
  hazchem_storage: string;
  hazchem_reserve: string;
  hazchem_fixed_workers: string;
}

/** 自定义字段编辑行：text=单值文本，multi=多值（一个 key 对应多个值） */
interface ExtraRow {
  key: string;
  type: "text" | "multi";
  textValue: string;
  multiValue: string[];
}

const emptyForm = (): EnterpriseForm => ({
  id: 0,
  name: "",
  code: "",
  industry: "",
  region: "",
  address: "",
  legal_person: "",
  person_in_charge: "",
  safety_officer: "",
  legal_person_phone: "",
  person_in_charge_phone: "",
  safety_officer_phone: "",
  enterprise_category: [],
  asset_attribute: "",
  employee_count: null,
  annual_output_value: null,
  fire_level: "",
  confined_space: [],
  has_natural_gas: false,
  is_injection_molding: false,
  has_dormitory: false,
  has_hazardous_chemicals: false,
  has_hazardous_waste: false,
  involves_dust_explosion: false,
  is_new_energy_welding: false,
  has_large_storage_tank: false,
  confined_space_count: "",
  confined_space_type: "",
  confined_space_workers: "",
  confined_space_hazards: "",
  confined_space_op_subject: "",
  confined_space_op_cycle: "",
  confined_space_fixed_workers: "",
  hazchem_names: "",
  hazchem_op_subject: "",
  hazchem_major_hazard: "",
  hazchem_state: "",
  hazchem_op_cycle: "",
  hazchem_usage: "",
  hazchem_annual_usage: "",
  hazchem_storage: "",
  hazchem_reserve: "",
  hazchem_fixed_workers: ""
});

const form = reactive<EnterpriseForm>(emptyForm());
const extraRows = ref<ExtraRow[]>([]);
/** 各自定义字段在历史数据中出现过的值（多值字段的下拉建议） */
const extraOptions = ref<Record<string, string[]>>({});
const submitLoading = ref(false);
const formRef = ref<FormInstance>();

// 编辑模式：原本为空的字段一律不校验（不阻塞其他字段的修改）；
// 原本有值的字段仅防误清空，不做格式校验（18位等检查仅新建时执行）
const rules = computed<FormRules>(() => {
  if (!isEdit.value) {
    return {
      name: [{ required: true, message: "请输入企业名称", trigger: "blur" }],
      code: [
        { required: true, message: "请输入信用代码", trigger: "blur" },
        { len: 18, message: "信用代码必须为18位", trigger: "blur" }
      ],
      industry: [{ required: true, message: "请选择行业", trigger: "change" }],
      region: [{ required: true, message: "请选择区域", trigger: "change" }]
    };
  }
  const editRules: FormRules = {
    name: [{ required: true, message: "请输入企业名称", trigger: "blur" }]
  };
  if (props.row?.code) {
    editRules.code = [
      { required: true, message: "请输入信用代码", trigger: "blur" }
    ];
  }
  return editRules;
});

// ── 字段裁剪（与原企业页一致：新建全量，编辑按当前页列裁剪）──
const pageColumn = (key: string) => props.pageColumns.includes(key);
const fieldShown = (key: string) => !isEdit.value || pageColumn(key);
const showBaseGroup = computed(() =>
  [
    "name",
    "code",
    "category",
    "asset",
    "industry",
    "region",
    "legal_person",
    "person_in_charge",
    "safety_officer",
    "address"
  ].some(k => fieldShown(k))
);
const showScaleGroup = computed(() =>
  ["employee_count", "annual_output_value"].some(k => fieldShown(k))
);
const showSafetyGroup = computed(
  () =>
    ["fire_level", "confined_space", "flags"].some(k => fieldShown(k)) ||
    pageColumn("confined_space_count") ||
    pageColumn("hazchem_names")
);
const showConfinedSpaceDetail = computed(() =>
  pageColumn("confined_space_count")
);
const showHazchemDetail = computed(() => pageColumn("hazchem_names"));
const showExtraGroup = computed(() => fieldShown("extra"));

const fetchExtraOptions = async () => {
  try {
    extraOptions.value = (await getEnterpriseExtraOptions()) || {};
  } catch {
    // 建议选项加载失败不影响表单使用，仍可自由输入
    extraOptions.value = {};
  }
};

/** 弹窗打开时初始化表单（新建清空 / 编辑回显） */
const onOpen = () => {
  // 先清除上一次残留的校验状态，避免 el-form 在值未回填前触发校验
  formRef.value?.clearValidate();
  const row = props.row;
  if (!row) {
    Object.assign(form, emptyForm());
    extraRows.value = [];
  } else {
    Object.assign(form, emptyForm(), {
      id: row.id,
      name: row.name,
      code: row.code ?? "",
      industry: row.industry ?? "",
      region: row.region,
      address: row.address,
      legal_person: row.legal_person,
      person_in_charge: row.person_in_charge ?? "",
      safety_officer: row.safety_officer ?? "",
      legal_person_phone: row.legal_person_phone ?? "",
      person_in_charge_phone: row.person_in_charge_phone ?? "",
      safety_officer_phone: row.safety_officer_phone ?? "",
      enterprise_category: Array.isArray(row.enterprise_category)
        ? [...row.enterprise_category]
        : [],
      asset_attribute: row.asset_attribute ?? "",
      employee_count: row.employee_count ?? null,
      annual_output_value: row.annual_output_value ?? null,
      fire_level: row.fire_level ?? "",
      confined_space: Array.isArray(row.confined_space)
        ? [...row.confined_space]
        : [],
      has_natural_gas: !!row.has_natural_gas,
      is_injection_molding: !!row.is_injection_molding,
      has_dormitory: !!row.has_dormitory,
      has_hazardous_chemicals: !!row.has_hazardous_chemicals,
      has_hazardous_waste: !!row.has_hazardous_waste,
      involves_dust_explosion: !!row.involves_dust_explosion,
      is_new_energy_welding: !!row.is_new_energy_welding,
      has_large_storage_tank: !!row.has_large_storage_tank,
      confined_space_count: row.confined_space_count ?? "",
      confined_space_type: row.confined_space_type ?? "",
      confined_space_workers: row.confined_space_workers ?? "",
      confined_space_hazards: row.confined_space_hazards ?? "",
      confined_space_op_subject: row.confined_space_op_subject ?? "",
      confined_space_op_cycle: row.confined_space_op_cycle ?? "",
      confined_space_fixed_workers: row.confined_space_fixed_workers ?? "",
      hazchem_names: row.hazchem_names ?? "",
      hazchem_op_subject: row.hazchem_op_subject ?? "",
      hazchem_major_hazard: row.hazchem_major_hazard ?? "",
      hazchem_state: row.hazchem_state ?? "",
      hazchem_op_cycle: row.hazchem_op_cycle ?? "",
      hazchem_usage: row.hazchem_usage ?? "",
      hazchem_annual_usage: row.hazchem_annual_usage ?? "",
      hazchem_storage: row.hazchem_storage ?? "",
      hazchem_reserve: row.hazchem_reserve ?? "",
      hazchem_fixed_workers: row.hazchem_fixed_workers ?? ""
    });
    // extra 回显：数组 → 多值行，字符串 → 文本行
    extraRows.value = Object.entries(row.extra || {}).map(([key, value]) =>
      Array.isArray(value)
        ? { key, type: "multi", textValue: "", multiValue: value.map(String) }
        : { key, type: "text", textValue: String(value), multiValue: [] }
    );
  }
  fetchExtraOptions();
};

/** 增删一行自定义字段（默认文本类型，可在行内切换为多值） */
const addExtraRow = () => {
  extraRows.value.push({
    key: "",
    type: "text",
    textValue: "",
    multiValue: []
  });
};
const removeExtraRow = (index: number) => {
  extraRows.value.splice(index, 1);
};

/** 字段名候选：所有企业已用过的 key（历史沉淀）∪ 本行当前输入的新 key */
const allExtraKeys = (row: ExtraRow): string[] => {
  const keys = Object.keys(extraOptions.value);
  const current = row.key.trim();
  if (current && !keys.includes(current)) keys.push(current);
  return keys.sort();
};

/** 某多值行的候选项 = 历史值 ∪ 当前已填值（去重） */
const multiCandidates = (row: ExtraRow): string[] => {
  const history = extraOptions.value[row.key.trim()] || [];
  return Array.from(new Set([...history, ...row.multiValue]));
};

/** 把编辑行组装成 extra 对象：空 key 丢弃；文本→字符串，多值→字符串数组 */
const buildExtra = (): Record<string, string | string[]> => {
  const extra: Record<string, string | string[]> = {};
  for (const row of extraRows.value) {
    const key = row.key.trim();
    if (!key) continue;
    extra[key] =
      row.type === "multi"
        ? row.multiValue.map(v => v.trim()).filter(Boolean)
        : row.textValue;
  }
  return extra;
};

/**
 * 组装提交载荷：按当前页可见列裁剪，未显示的字段不提交——
 * 后端 update_enterprise 用 model_fields_set 做 PATCH 语义，未提交列保持原值，
 * 避免弹窗未显示的详细字段被空值覆盖清空。
 */
const buildPayload = (): Partial<Enterprise> => {
  const p: Partial<Enterprise> = {};
  if (fieldShown("name")) p.name = form.name;
  if (fieldShown("code")) p.code = form.code;
  if (fieldShown("category")) p.enterprise_category = form.enterprise_category;
  if (fieldShown("asset")) p.asset_attribute = form.asset_attribute;
  if (fieldShown("industry")) p.industry = form.industry;
  if (fieldShown("region")) p.region = form.region;
  if (fieldShown("legal_person")) {
    p.legal_person = form.legal_person;
    p.legal_person_phone = form.legal_person_phone;
  }
  if (fieldShown("person_in_charge")) {
    p.person_in_charge = form.person_in_charge;
    p.person_in_charge_phone = form.person_in_charge_phone;
  }
  if (fieldShown("safety_officer")) {
    p.safety_officer = form.safety_officer;
    p.safety_officer_phone = form.safety_officer_phone;
  }
  if (fieldShown("address")) p.address = form.address;
  // 数字框清空为 undefined，需显式转 null，后端才会置空而不是"不改"
  if (fieldShown("employee_count"))
    p.employee_count = form.employee_count ?? null;
  if (fieldShown("annual_output_value"))
    p.annual_output_value = form.annual_output_value ?? null;
  if (fieldShown("fire_level")) p.fire_level = form.fire_level;
  if (fieldShown("confined_space")) p.confined_space = form.confined_space;
  if (fieldShown("flags")) {
    p.has_natural_gas = form.has_natural_gas;
    p.is_injection_molding = form.is_injection_molding;
    p.has_dormitory = form.has_dormitory;
    p.has_hazardous_chemicals = form.has_hazardous_chemicals;
    p.has_hazardous_waste = form.has_hazardous_waste;
    p.involves_dust_explosion = form.involves_dust_explosion;
    p.is_new_energy_welding = form.is_new_energy_welding;
    p.has_large_storage_tank = form.has_large_storage_tank;
  }
  // 有限空间详情（仅当前页含该列才提交）
  if (pageColumn("confined_space_count")) {
    p.confined_space_count = form.confined_space_count;
    p.confined_space_type = form.confined_space_type;
    p.confined_space_workers = form.confined_space_workers;
    p.confined_space_hazards = form.confined_space_hazards;
    p.confined_space_op_subject = form.confined_space_op_subject;
    p.confined_space_op_cycle = form.confined_space_op_cycle;
    p.confined_space_fixed_workers = form.confined_space_fixed_workers;
  }
  // 危化品详情（仅当前页含该列才提交）
  if (pageColumn("hazchem_names")) {
    p.hazchem_names = form.hazchem_names;
    p.hazchem_op_subject = form.hazchem_op_subject;
    p.hazchem_major_hazard = form.hazchem_major_hazard;
    p.hazchem_state = form.hazchem_state;
    p.hazchem_op_cycle = form.hazchem_op_cycle;
    p.hazchem_usage = form.hazchem_usage;
    p.hazchem_annual_usage = form.hazchem_annual_usage;
    p.hazchem_storage = form.hazchem_storage;
    p.hazchem_reserve = form.hazchem_reserve;
    p.hazchem_fixed_workers = form.hazchem_fixed_workers;
  }
  if (fieldShown("extra")) p.extra = buildExtra();
  return p;
};

const handleSubmit = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    submitLoading.value = true;
    try {
      if (isEdit.value) {
        await updateEnterprise(form.id, buildPayload());
        ElMessage.success("更新成功");
      } else {
        await createEnterprise(buildPayload());
        ElMessage.success("创建成功");
      }
      emit("update:modelValue", false);
      emit("saved");
    } catch (e: any) {
      const msg =
        e?.response?.data?.detail ||
        e?.response?.data?.message ||
        e?.message ||
        (isEdit.value ? "更新失败" : "创建失败");
      ElMessage.error(msg);
    } finally {
      submitLoading.value = false;
    }
  });
};
</script>

<style scoped>
/* 表单分组标题：基础信息/规模信息/安全信息 */
.form-section-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  padding-left: 8px;
  margin: 4px 0 12px;
  border-left: 3px solid var(--el-color-primary);
  line-height: 16px;
}
.form-section-title:not(:first-child) {
  margin-top: 8px;
}

/* 安全风险点开关：四列网格排列 */
.safety-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  row-gap: 10px;
  width: 100%;
}
.safety-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 自定义字段行：输入/多选框在 flex 行内正确收缩并撑满剩余宽度 */
.flex-1 {
  min-width: 0;
}
</style>
