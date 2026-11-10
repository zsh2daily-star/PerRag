<!--
  统一表格基座 ReTablePage —— 全部表格页共用同一套逻辑。

  页面差异全部通过 config（TablePageConfig）声明：
    - 列（显示/筛选/导出）→ columns
    - 数据源               → fetchApi（POST /{table}/query 统一筛选协议）
    - 批量修改/删除/导入/导出 → bulkEditFields + bulkUpdateApi / deleteApi / importApi
    - 列偏好持久化          → storageKey

  页面个性化通过具名插槽注入：
    - #cell-<key>     自定义单元格（列配置 slot 指向）
    - #expand         行内展开
    - #actions        操作列按钮（替换默认删除按钮）
    - #actions-prepend 在默认删除按钮前追加操作按钮
    - #toolbar-extra  工具栏右侧追加按钮（新建/上传等）
    - #header-extra   表头筛选弹窗追加内容（透传 ReFilterHeader 的 extra 插槽）

  用法：
    <ReTablePage :config="config">
      <template #cell-status="{ row }">...</template>
      <template #actions="{ row, refresh }">...</template>
    </ReTablePage>
-->
<template>
  <div class="p-4">
    <!-- 关键词搜索（配置 keywordPlaceholder 才显示） -->
    <div v-if="config.keywordPlaceholder" class="mb-4">
      <el-input
        v-model="store.keyword.value"
        :placeholder="config.keywordPlaceholder"
        clearable
        style="width: 360px"
        @keyup.enter="store.handleKeywordSearch"
        @clear="store.handleKeywordSearch"
      >
        <template #append>
          <el-button @click="store.handleKeywordSearch">搜索</el-button>
        </template>
      </el-input>
      <el-button class="ml-2" @click="store.handleReset">重置</el-button>
    </div>

    <el-card shadow="never">
      <!-- 工具栏 -->
      <div class="flex justify-between items-center mb-4">
        <span class="font-bold">{{ config.title }}</span>
        <div class="flex items-center">
          <el-popover
            :width="260"
            placement="bottom-end"
            trigger="click"
            :popper-class="`${config.storageKey}-column-popper`"
          >
            <template #reference>
              <el-button class="mr-2">列显示</el-button>
            </template>
            <ReColumnSettings
              v-model:visibleKeys="store.visibleColumns.value"
              :columns="store.panelColumnItems.value"
              :groups="store.panelGroups.value"
              @change="store.onColumnPrefsChange"
              @select-all="store.selectAllColumns"
              @clear-all="store.clearAllColumns"
              @invert="store.invertColumns"
              @toggle-group="store.toggleGroupColumns"
            />
          </el-popover>

          <el-upload
            v-if="config.importApi"
            :show-file-list="false"
            :before-upload="store.handleImport"
            accept=".xlsx,.xls"
            :disabled="store.importLoading.value"
            class="mr-2"
          >
            <el-button type="success" :loading="store.importLoading.value">
              {{ config.importText ?? "导入Excel" }}
            </el-button>
          </el-upload>

          <el-dropdown
            class="mr-2"
            trigger="click"
            @command="(cmd: 'filter' | 'selected') => store.handleExport(cmd)"
          >
            <el-button :loading="store.exporting.value">
              导出 Excel <el-icon class="el-icon--right">▾</el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="filter">
                  导出全部（按筛选条件）
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="store.selectedRows.value.length"
                  command="selected"
                >
                  导出选中（{{ store.selectedRows.value.length }}）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <slot name="toolbar-extra" :refresh="store.fetchList" />

          <el-button
            v-if="
              config.bulkEditFields?.length && store.selectedRows.value.length
            "
            type="warning"
            class="ml-2"
            @click="store.bulkEditVisible.value = true"
          >
            批量修改（{{ store.selectedRows.value.length }}）
          </el-button>
          <el-button
            v-if="config.deleteApi && store.selectedRows.value.length"
            type="danger"
            class="ml-2"
            :loading="store.loading.value"
            @click="store.handleBatchDelete"
          >
            删除选中（{{ store.selectedRows.value.length }}）
          </el-button>
        </div>
      </div>

      <!-- 表格 -->
      <el-table
        v-if="store.showTable.value"
        ref="elTableRef"
        v-loading="store.loading.value"
        :data="store.tableData.value"
        :row-key="config.rowKey ?? 'id'"
        :span-method="config.spanMethod"
        :row-class-name="config.rowClassName"
        border
        stripe
        style="width: 100%"
        @selection-change="store.handleSelectionChange"
        @vue:mounted="store.onTableMounted"
      >
        <el-table-column
          v-if="hasSelection"
          type="selection"
          width="42"
          fixed="left"
        />
        <el-table-column v-if="$slots.expand" type="expand">
          <template #default="{ row }">
            <slot name="expand" :row="row" :refresh="store.fetchList" />
          </template>
        </el-table-column>

        <template v-for="col in store.orderedColumns.value" :key="col.key">
          <el-table-column
            v-if="col.alwaysVisible || store.isKeyVisible(col.key)"
            :prop="col.prop"
            :label="col.label"
            :width="col.width"
            :min-width="col.minWidth"
            :align="col.align"
            :fixed="col.fixed"
            :show-overflow-tooltip="col.editable ? false : (col.showOverflowTooltip ?? true)"
          >
            <template #header>
              <slot
                v-if="col.headerSlot"
                :name="col.headerSlot"
                :column="col"
              />
              <ReFilterHeader
                v-else-if="col.filterType"
                :filters="store.hf"
                :field="col.filterField ?? col.prop ?? col.key"
                :type="col.filterType"
                :label="col.label"
                :options="resolveFilterOptions(col)"
                :placeholder="col.filterPlaceholder"
                :null-option="col.filterNullOption"
                :null-label="col.filterNullLabel"
              >
                <template #extra>
                  <slot name="header-extra" :column="col" />
                </template>
              </ReFilterHeader>
              <span v-else>{{ col.label }}</span>
            </template>
            <template v-if="col.slot" #default="{ row, $index }">
              <slot
                :name="col.slot"
                :row="row"
                :index="$index"
                :refresh="store.fetchList"
              />
            </template>
            <template v-else-if="col.editable && config.inlineUpdateApi" #default="{ row }">
              <EditableCell
                :value="row[col.prop ?? col.key]"
                :label="col.label"
                :update-fn="async (v: string) => {
                  await config.inlineUpdateApi!(row, col.editable!, v);
                  (row as any)[col.prop ?? col.key] = v;
                }"
              />
            </template>
            <template v-else-if="col.formatter" #default="{ row }">
              {{ col.formatter(row) }}
            </template>
          </el-table-column>
        </template>

        <!-- 操作列 -->
        <el-table-column
          v-if="$slots.actions || config.deleteApi"
          label="操作"
          :width="actionWidth"
          fixed="right"
          align="center"
        >
          <template #default="{ row, $index }">
            <slot
              name="actions-prepend"
              :row="row"
              :index="$index"
              :refresh="store.fetchList"
            />
            <slot
              name="actions"
              :row="row"
              :index="$index"
              :refresh="store.fetchList"
              :remove="() => store.handleDelete(row)"
            >
              <el-button
                v-if="config.deleteApi"
                link
                type="danger"
                size="small"
                @click="store.handleDelete(row)"
              >
                删除
              </el-button>
            </slot>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="flex justify-end mt-4">
        <el-pagination
          v-model:current-page="store.query.page"
          v-model:page-size="store.query.page_size"
          :total="store.total.value"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="store.handlePageChange"
          @size-change="store.handleSizeChange"
        />
      </div>
    </el-card>

    <!-- 批量修改弹窗 -->
    <ReBulkEdit
      v-if="config.bulkEditFields?.length"
      v-model="store.bulkEditVisible.value"
      :fields="config.bulkEditFields"
      :selected-count="store.selectedRows.value.length"
      :loading="store.bulkEditLoading.value"
      @confirm="store.handleBulkEdit"
    />

    <!-- 导入结果弹窗（页面可用 #import-result 自定义内容） -->
    <el-dialog
      v-model="store.importResultVisible.value"
      title="导入结果"
      :width="config.importResultWidth ?? 520"
      destroy-on-close
    >
      <div v-if="store.importResult.value">
        <slot name="import-result" :result="store.importResult.value">
          <el-alert
            :type="
              store.importResult.value.success === false ? 'error' : 'success'
            "
            :title="
              store.importResult.value.message ||
              (store.importResult.value.success === false
                ? '导入失败'
                : '导入完成')
            "
            :closable="false"
            class="mb-3"
          />
          <pre v-if="importResultText" class="import-result-detail">{{
            importResultText
          }}</pre>
        </slot>
      </div>
      <el-skeleton v-else :rows="3" animated />
    </el-dialog>
  </div>
</template>

<script setup lang="ts" generic="T extends Record<string, any>">
import { computed, onMounted, ref, watch } from "vue";
import ReFilterHeader from "@/components/ReFilterHeader/index.vue";
import ReColumnSettings from "@/components/ReColumnSettings/index.vue";
import ReBulkEdit from "@/components/ReBulkEdit/index.vue";
import EditableCell from "@/components/EditableCell/index.vue";
import { useTablePage } from "./useTablePage";
import type {
  TablePageConfig,
  TablePageExpose,
  TableColumnConfig
} from "./types";

const props = defineProps<{ config: TablePageConfig<T> }>();

defineSlots<
  {
    /** 工具栏右侧追加按钮（新建/上传等） */
    "toolbar-extra"?: (props: { refresh: () => Promise<void> }) => any;
    /** 表头筛选弹窗追加内容 */
    "header-extra"?: (props: { column: TableColumnConfig<T> }) => any;
    /** 导入结果弹窗自定义内容（默认渲染 message + 其余字段 JSON） */
    "import-result"?: (props: { result: any }) => any;
    /** 行内展开 */
    expand?: (props: { row: T; refresh: () => Promise<void> }) => any;
    /** 默认删除按钮前追加操作按钮 */
    "actions-prepend"?: (props: {
      row: T;
      index: number;
      refresh: () => Promise<void>;
    }) => any;
    /** 操作列（整体替换默认删除按钮） */
    actions?: (props: {
      row: T;
      index: number;
      refresh: () => Promise<void>;
      remove: () => Promise<boolean>;
    }) => any;
  } & {
    /** 自定义单元格：名称由列配置 slot 指定（约定 cell-<key>） */
    [slot in `cell-${string}`]?: (props: {
      row: T;
      index: number;
      refresh: () => Promise<void>;
    }) => any;
  } & {
    /** 自定义表头：名称由列配置 headerSlot 指定（约定 header-<key>） */
    [slot in `header-${string}`]?: (props: {
      column: TableColumnConfig<T>;
    }) => any;
  }
>();

const store = useTablePage(props.config);

// el-table 实例转发给 composable 内部 tableRef（多选清空/拖拽用）
const elTableRef = ref();
watch(
  elTableRef,
  v => {
    if (v) store.tableRef.value = v;
  },
  { flush: "post" }
);

/** 有批量修改或批量删除时才渲染多选列 */
const hasSelection = computed(
  () => !!(props.config.bulkEditFields?.length || props.config.deleteApi)
);

/** 操作列宽度：slot 自定义时给足空间 */
const actionWidth = computed(() => (props.config.deleteApi ? 220 : 160));

/** 导入结果详情（把响应体中非 message/success 字段格式化展示） */
const importResultText = computed(() => {
  const r = store.importResult.value;
  if (!r || typeof r !== "object") return "";
  const { success, message, ...rest } = r as Record<string, any>;
  const entries = Object.entries(rest).filter(
    ([, v]) => v !== undefined && v !== null && v !== ""
  );
  if (!entries.length) return "";
  return entries
    .map(
      ([k, v]) =>
        `${k}: ${typeof v === "object" ? JSON.stringify(v, null, 2) : v}`
    )
    .join("\n");
});

/** 解析列筛选选项（支持静态数组或函数返回动态选项） */
const resolveFilterOptions = (col: TableColumnConfig<T>) =>
  typeof col.filterOptions === "function"
    ? col.filterOptions()
    : (col.filterOptions ?? []);

onMounted(() => {
  store.loadColumnPrefs();
  store.fetchList();
});

defineExpose<TablePageExpose<T>>({
  reload: store.fetchList,
  refresh: store.handleSearch,
  rows: store.tableData,
  total: store.total,
  loading: store.loading,
  selectedRows: store.selectedRows,
  filter: store.filter,
  hf: store.hf,
  clearSelection: store.clearSelection,
  runExport: store.handleExport
});
</script>

<style scoped>
.import-result-detail {
  max-height: 320px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 8px 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ── 表格视觉统一 ───────────────────────────────────────── */
/* 表头：浅底 + 加粗 + 主色文字，与内容区区分 */
:deep(.el-table th.el-table__cell) {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  font-weight: 600;
}
/* 单元格：统一行高与内边距，垂直居中 */
:deep(.el-table td.el-table__cell) {
  padding: 8px 0;
}
:deep(.el-table .cell) {
  line-height: 1.5;
}
/* 单元格内多标签：相邻标签之间留出间距，避免挤成一团 */
:deep(.el-table .cell .el-tag + .el-tag) {
  margin-left: 4px;
}
</style>
