<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { Filter } from "@element-plus/icons-vue";
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import ReTablePage from "@/components/ReTablePage/index.vue";
import type {
  TablePageConfig,
  TablePageExpose
} from "@/components/ReTablePage/types";
import type { Enterprise } from "@/api/enterprises";
import { getEnterpriseRegions } from "@/api/enterprises";
import EnterpriseFormDialog from "./EnterpriseFormDialog.vue";
import {
  buildEnterpriseConfig,
  categoryTagType,
  hasAnyFlag,
  extraEntries,
  safetyFlags
} from "./table.config";

defineOptions({ name: "Enterprises" });

const router = useRouter();

const props = defineProps<{
  presetFilter?: FilterGroup | null;
  columnKeys?: string[];
  /** 列偏好存储键（专页互不污染） */
  columnStorageKey?: string;
  columnOrderStorageKey?: string;
}>();

const tableRef = ref<TablePageExpose<Enterprise>>();

// ── 区域动态选项（列筛选 + 编辑弹窗共享）────────────────────
const regionOptions = ref<string[]>([]);
const loadRegions = async () => {
  try {
    regionOptions.value = (await getEnterpriseRegions()) ?? [];
  } catch {
    regionOptions.value = [];
  }
};

// ── 风险标记组合列：多 flag 布尔筛选（本页专用，非基座通用能力）──
const boolFilterValues = reactive<Record<string, "true" | "false" | "">>({});
const hasBoolFilter = computed(() =>
  safetyFlags.some(f => boolFilterValues[f.key]) ||
  boolFilterValues["confined_space"]
);
/** 从条件组移除指定字段的全部条件 */
const removeFieldConditions = (group: FilterGroup, field: string) => {
  for (let i = group.children.length - 1; i >= 0; i--) {
    const n = group.children[i];
    const hit =
      n.type === "leaf"
        ? n.field === field
        : n.type === "group" &&
          n.children.some(c => c.type === "leaf" && c.field === field);
    if (hit) group.children.splice(i, 1);
  }
};
/** 基座经 defineExpose 暴露的 filter（proxyRefs 已解包为 FilterGroup） */
const rootFilter = (): FilterGroup | null =>
  (tableRef.value?.filter as unknown as FilterGroup) ?? null;

const applyBoolFilter = () => {
  const group = rootFilter();
  if (!group) return;
  // 有限空间（数组列，用 is_not_null / is_null）
  removeFieldConditions(group, "confined_space");
  const csVal = boolFilterValues["confined_space"];
  if (csVal === "true") {
    group.children.push({
      type: "leaf",
      field: "confined_space",
      operator: "is_not_null",
      value: null
    });
  } else if (csVal === "false") {
    group.children.push({
      type: "leaf",
      field: "confined_space",
      operator: "is_null",
      value: null
    });
  }
  // 布尔风险标记
  for (const flag of safetyFlags) {
    removeFieldConditions(group, flag.key);
    const val = boolFilterValues[flag.key];
    if (val === "true") {
      group.children.push({
        type: "leaf",
        field: flag.key,
        operator: "is_true",
        value: null
      });
    } else if (val === "false") {
      group.children.push({
        type: "leaf",
        field: flag.key,
        operator: "is_false",
        value: null
      });
    }
  }
  tableRef.value?.refresh();
};
const clearBoolFilter = () => {
  const group = rootFilter();
  boolFilterValues["confined_space"] = "";
  if (group) removeFieldConditions(group, "confined_space");
  for (const flag of safetyFlags) {
    boolFilterValues[flag.key] = "";
    if (group) removeFieldConditions(group, flag.key);
  }
  tableRef.value?.refresh();
};

// ── 表格配置（列定义 + 分组 + 筛选/导出/批量修改全部派生）──
const config = computed<TablePageConfig<Enterprise>>(() => ({
  ...buildEnterpriseConfig({
    storageKey: props.columnStorageKey ?? "enterprise",
    title: "企业列表",
    columnKeys: props.columnKeys,
    presetFilter: props.presetFilter ?? undefined,
    regionOptions
  }),
  onReset: () => {
    for (const k of Object.keys(boolFilterValues)) boolFilterValues[k] = "";
  }
}));

/** 当前页可用列 key（专页裁剪后的列集，表单弹窗字段裁剪依据） */
const pageColumns = computed(() => config.value.columns.map(c => c.key));

// ── 编辑弹窗 ──────────────────────────────────────────────
const formVisible = ref(false);
const editingRow = ref<Enterprise | null>(null);
const openCreate = () => {
  editingRow.value = null;
  formVisible.value = true;
};
const openEdit = (row: Enterprise) => {
  editingRow.value = row;
  formVisible.value = true;
};

/** 打开企业档案（介绍页） */
const openProfile = (row: Enterprise) => {
  router.push({ name: "EnterpriseProfile", query: { id: row.id } });
};

onMounted(loadRegions);
</script>

<template>
  <div>
    <ReTablePage ref="tableRef" :config="config">
    <!-- 工具栏：新建按钮 -->
    <template #toolbar-extra>
      <el-button type="primary" class="mr-2" @click="openCreate">
        新建企业
      </el-button>
    </template>

    <!-- 风险标记列：组合多 flag 布尔筛选（本页专用） -->
    <template #header-flags>
      <div class="th-filter">
        <span>风险标记</span>
        <el-popover trigger="click" placement="bottom" width="200">
          <template #reference>
            <el-icon
              class="th-filter__icon"
              :class="{ 'is-active': hasBoolFilter }"
            >
              <Filter />
            </el-icon>
          </template>
          <div class="th-filter__panel">
            <div class="th-filter__bool-row">
              <span>有限空间</span>
              <el-select
                v-model="boolFilterValues['confined_space']"
                placeholder="不限"
                size="small"
                style="width: 90px"
              >
                <el-option label="是" value="true" />
                <el-option label="否" value="false" />
              </el-select>
            </div>
            <div
              v-for="f in safetyFlags"
              :key="f.key"
              class="th-filter__bool-row"
            >
              <span>{{ f.label }}</span>
              <el-select
                v-model="boolFilterValues[f.key]"
                placeholder="不限"
                size="small"
                style="width: 90px"
              >
                <el-option label="是" value="true" />
                <el-option label="否" value="false" />
              </el-select>
            </div>
            <div class="th-filter__btns">
              <el-button size="small" @click="clearBoolFilter">重置</el-button>
              <el-button size="small" type="primary" @click="applyBoolFilter">
                筛选
              </el-button>
            </div>
          </div>
        </el-popover>
      </div>
    </template>

    <!-- 自定义单元格 -->
    <template #cell-category="{ row }: { row: Enterprise }">
      <template v-if="(row.enterprise_category || []).length">
        <el-tag
          v-for="cat in row.enterprise_category"
          :key="cat"
          size="small"
          effect="plain"
          class="mr-1"
          :type="categoryTagType(cat)"
        >
          {{ cat }}
        </el-tag>
      </template>
      <span v-else>—</span>
    </template>

    <template #cell-asset="{ row }: { row: Enterprise }">
      <el-tag v-if="row.asset_attribute" size="small" type="success">
        {{ row.asset_attribute }}
      </el-tag>
      <span v-else>—</span>
    </template>

    <template #cell-industry="{ row }: { row: Enterprise }">
      <el-tag size="small">{{ row.industry || "—" }}</el-tag>
    </template>

    <template #cell-region="{ row }: { row: Enterprise }">
      <el-tag size="small" type="info">{{ row.region || "—" }}</el-tag>
    </template>

    <template #cell-employee_count="{ row }: { row: Enterprise }">
      {{ row.employee_count != null ? row.employee_count + " 人" : "—" }}
    </template>

    <template #cell-fire_level="{ row }: { row: Enterprise }">
      <el-tag
        v-if="row.fire_level"
        size="small"
        :type="row.fire_level === '重点单位' ? 'danger' : 'warning'"
        :effect="row.fire_level === '重点单位' ? 'dark' : 'plain'"
      >
        {{ row.fire_level }}
      </el-tag>
      <span v-else>—</span>
    </template>

    <template #cell-confined_space="{ row }: { row: Enterprise }">
      <template v-if="(row.confined_space || []).length">
        <el-tag
          v-for="cs in row.confined_space"
          :key="cs"
          size="small"
          type="warning"
          effect="plain"
          class="mr-1"
        >
          {{ cs }}
        </el-tag>
      </template>
      <span v-else>—</span>
    </template>

    <template #cell-flags="{ row }: { row: Enterprise }">
      <template v-if="hasAnyFlag(row)">
        <el-tag
          v-if="(row.confined_space || []).length"
          key="confined_space"
          size="small"
          type="warning"
          effect="plain"
          class="mr-1"
        >
          有限空间
        </el-tag>
        <el-tag
          v-for="f in safetyFlags.filter(flag => row[flag.key])"
          :key="f.key"
          size="small"
          :type="f.key === 'has_dormitory' ? 'info' : 'danger'"
          effect="plain"
          class="mr-1"
        >
          {{ f.label }}
        </el-tag>
      </template>
      <span v-else>—</span>
    </template>

    <template #cell-extra="{ row }: { row: Enterprise }">
      <div v-if="extraEntries(row).length">
        <div v-for="[k, v] in extraEntries(row)" :key="k" class="extra-line">
          <span class="extra-line__key">{{ k }}：</span>
          <span>{{ v }}</span>
        </div>
      </div>
      <span v-else>—</span>
    </template>

    <!-- 操作列：编辑 + 删除 -->
    <template
      #actions="{ row, remove }: { row: Enterprise; remove: () => void }"
    >
      <el-button link type="primary" size="small" @click="openProfile(row)">
        详情
      </el-button>
      <el-button link type="primary" size="small" @click="openEdit(row)">
        编辑
      </el-button>
      <el-button link type="danger" size="small" @click="remove()">
        删除
      </el-button>
    </template>
  </ReTablePage>

  <EnterpriseFormDialog
    v-model="formVisible"
    :row="editingRow"
    :page-columns="pageColumns"
    :region-options="regionOptions"
    @saved="tableRef?.reload()"
  />
  </div>
</template>

<style scoped>
/* 表头筛选 UI（风险标记组合列复用） */
.th-filter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.th-filter__icon {
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
}
.th-filter__icon.is-active {
  color: var(--el-color-primary);
}
.th-filter__panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.th-filter__bool-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}
.th-filter__btns {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 自定义信息汇总列 */
.extra-line {
  line-height: 1.6;
}
.extra-line__key {
  color: var(--el-text-color-secondary);
}
</style>
