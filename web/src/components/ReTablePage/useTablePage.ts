/**
 * 统一表格页 composable —— ReTablePage 基座的逻辑内核。
 *
 * 收口全部表格页重复逻辑：
 *   - 数据加载（POST /{table}/query 统一筛选协议）+ 分页
 *   - 关键词 upsert（keyword 虚拟字段 leaf 合并进筛选树根组）
 *   - 表头筛选（复用 useHeaderFilters，数组枚举列自动识别）
 *   - 列显隐 + 顺序偏好（复用 useColumnPrefs，支持分组面板）
 *   - 表头拖拽排序（复用 useColumnDrag）
 *   - 批量修改 / 批量删除（Promise.allSettled 逐条 + 成败汇总）
 *   - Excel 导出（按可见列 + 导出格式化）/ Excel 导入（含结果弹窗状态）
 *
 * 页面差异全部由 TablePageConfig 声明；本文件不含任何具体业务字段。
 */
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import * as XLSX from "xlsx";
import type {
  FilterGroup,
  FilterLeaf
} from "@/components/ReFilterBuilder/types";
import {
  makeGroup,
  makeDefaultKeywordLeaf
} from "@/components/ReFilterBuilder/types";
import { useHeaderFilters } from "@/composables/useHeaderFilters";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useColumnDrag } from "@/composables/useColumnDrag";
import type {
  TablePageConfig,
  TableColumnConfig
} from "@/components/ReTablePage/types";

export function useTablePage<T extends Record<string, any>>(
  config: TablePageConfig<T>
) {
  const {
    storageKey,
    fetchApi,
    columns,
    rowKey = "id",
    exportBatchSize = 5000
  } = config;

  // ── 列表数据 / 分页 ─────────────────────────────────────
  const loading = ref(false);
  const tableData = ref<T[]>([]) as import("vue").Ref<T[]>;
  const total = ref(0);
  const tableRef = ref();
  const showTable = ref(true);

  const query = reactive({ page: 1, page_size: 20 });

  /** 筛选条件根组（嵌套 AND/OR 树），预设筛选不可被重置清除 */
  const buildRootGroup = (): FilterGroup =>
    config.presetFilter
      ? makeGroup("AND", [config.presetFilter, makeDefaultKeywordLeaf()])
      : makeGroup("AND", [makeDefaultKeywordLeaf()]);
  const filter = ref<FilterGroup>(buildRootGroup());

  const fetchList = async () => {
    loading.value = true;
    try {
      const res = await fetchApi(filter.value, query.page, query.page_size);
      const list = config.transformRows
        ? config.transformRows(res.list)
        : res.list;
      tableData.value = list;
      total.value = res.total;
      config.onRowsLoaded?.(list);
    } catch {
      ElMessage.error(`加载${config.title}失败`);
    } finally {
      loading.value = false;
    }
  };

  const handleSearch = () => {
    query.page = 1;
    fetchList();
  };

  // ── 关键词搜索（keyword 虚拟字段 → 后端多字段 OR like）──
  const keyword = ref("");
  const handleKeywordSearch = () => {
    const group = filter.value;
    const idx = group.children.findIndex(
      n => n.type === "leaf" && n.field === "keyword"
    );
    const leaf: FilterLeaf = {
      type: "leaf",
      field: "keyword",
      operator: "like",
      value: keyword.value
    };
    if (idx >= 0) group.children[idx] = leaf;
    else group.children.unshift(leaf);
    handleSearch();
  };

  // ── 表头筛选（text/number/date/enum/bool → 同一棵筛选树）──
  const arrayEnumFields = columns
    .filter(c => c.filterArrayEnum && c.filterField)
    .map(c => c.filterField as string);
  const hf = useHeaderFilters(filter, handleSearch, arrayEnumFields);

  const handleReset = () => {
    keyword.value = "";
    filter.value = buildRootGroup();
    hf.reset();
    config.onReset?.();
    handleSearch();
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

  // ── 列显隐 + 顺序偏好（localStorage 持久化）──────────────
  /** 参与列显隐勾选的列（alwaysVisible 列始终显示、不进面板） */
  const toggleableColumns = columns.filter(c => !c.alwaysVisible);
  const toggleableKeys = toggleableColumns.map(c => c.key);

  const columnPrefs = useColumnPrefs({
    allKeys: toggleableKeys,
    defaultVisible: (config.defaultVisible ?? toggleableKeys).filter(k =>
      toggleableKeys.includes(k)
    ),
    storageKey: `table-cols:${storageKey}`,
    orderStorageKey: `table-cols:${storageKey}:order`,
    migrate: config.migrateColumnPrefs
  });
  const {
    visibleColumns,
    columnOrder,
    orderedKeys,
    isKeyVisible,
    loadColumnPrefs,
    onColumnPrefsChange,
    onColumnOrderChange,
    selectAllColumns,
    clearAllColumns,
    invertColumns
  } = columnPrefs;

  /** 列显示面板数据（带分组） */
  const panelColumnItems = computed(() =>
    toggleableColumns.map(c => {
      const group = config.columnGroups?.find(g => g.keys.includes(c.key));
      return { key: c.key, label: c.label, group: group?.name };
    })
  );
  const panelGroups = computed(() => {
    if (!config.columnGroups?.length) return [];
    return config.columnGroups
      .filter(g => g.keys.some(k => toggleableKeys.includes(k)))
      .map(g => g.name);
  });
  const toggleGroupColumns = (groupName: string) => {
    const g = config.columnGroups?.find(x => x.name === groupName);
    if (!g) return;
    columnPrefs.toggleGroupColumns(
      g.keys.filter(k => toggleableKeys.includes(k))
    );
  };

  /**
   * 最终渲染列序：
   * - alwaysVisible 列不进列偏好，按 columns 声明位置锚定（如企业「名称」列始终在最前）；
   * - 可勾选列按用户保存的 columnOrder 顺序填充到其余位置。
   */
  const orderedColumns = computed<TableColumnConfig<T>[]>(() => {
    const byKey = new Map(columns.map(c => [c.key, c]));
    const userOrdered = orderedKeys.value
      .map(k => byKey.get(k))
      .filter(Boolean) as TableColumnConfig<T>[];
    let i = 0;
    return columns
      .map(c => (c.alwaysVisible ? c : userOrdered[i++]))
      .filter(Boolean) as TableColumnConfig<T>[];
  });

  // ── 表头拖拽排序（固定列 alwaysVisible 不参与拖拽）──────
  const dragColumnOptions = computed(() =>
    columns
      .filter(c => !c.alwaysVisible)
      .map(c => ({ key: c.key, label: c.label }))
  );
  const { onTableMounted } = useColumnDrag(
    tableRef,
    dragColumnOptions,
    columnOrder,
    onColumnOrderChange,
    showTable
  );

  // ── 多选 ────────────────────────────────────────────────
  const selectedRows = ref<T[]>([]) as import("vue").Ref<T[]>;
  const handleSelectionChange = (rows: T[]) => {
    selectedRows.value = rows;
  };
  const clearSelection = () => {
    tableRef.value?.clearSelection();
    selectedRows.value = [];
  };

  const rowDisplayName = (row: T): string =>
    config.rowDisplayName?.(row) ??
    row.name ??
    row.filename ??
    `#${row[rowKey]}`;

  /** Promise.allSettled 逐条执行 + 成败汇总（批量修改/删除共用） */
  const runBatch = async (
    rows: T[],
    action: (row: T) => Promise<unknown>,
    verb: string
  ) => {
    const results = await Promise.allSettled(rows.map(action));
    let ok = 0;
    const failed: string[] = [];
    results.forEach((r, i) => {
      const rejected =
        r.status === "rejected" ||
        (r.status === "fulfilled" && (r.value as any)?.success === false);
      if (rejected) {
        const reason =
          r.status === "rejected"
            ? r.reason?.response?.data?.detail ||
              r.reason?.response?.data?.message ||
              r.reason?.message
            : (r.value as any)?.message;
        failed.push(
          reason
            ? `${rowDisplayName(rows[i])}（${reason}）`
            : rowDisplayName(rows[i])
        );
      } else {
        ok += 1;
      }
    });
    if (ok) ElMessage.success(`成功${verb} ${ok} 条`);
    if (failed.length) {
      ElMessage({
        type: "warning",
        message: `${failed.length} 条${verb}失败：${failed.join("；")}`,
        duration: 6000,
        showClose: true
      });
    }
  };

  // ── 批量修改 ────────────────────────────────────────────
  const bulkEditVisible = ref(false);
  const bulkEditLoading = ref(false);
  const bulkFieldLabel = (k: string) =>
    config.bulkEditFields?.find(f => f.key === k)?.label || k;
  const handleBulkEdit = ({ field, value }: { field: string; value: any }) => {
    const rows = selectedRows.value;
    if (!rows.length || !config.bulkUpdateApi) return;
    ElMessageBox.confirm(
      `确定将选中的 ${rows.length} 条记录的「${bulkFieldLabel(field)}」统一修改为新值吗？`,
      "批量修改确认",
      { type: "warning" }
    )
      .then(async () => {
        bulkEditLoading.value = true;
        await runBatch(
          rows,
          row => config.bulkUpdateApi!(row, { [field]: value }),
          "修改"
        );
        bulkEditLoading.value = false;
        bulkEditVisible.value = false;
        clearSelection();
        fetchList();
      })
      .catch(() => {});
  };

  // ── 批量删除 ────────────────────────────────────────────
  const handleBatchDelete = () => {
    const rows = selectedRows.value;
    if (!rows.length || !config.deleteApi) return;
    const text = (
      config.batchDeleteConfirmText ?? "确定要删除选中的 {n} 条记录吗？"
    ).replace("{n}", String(rows.length));
    ElMessageBox.confirm(text, "批量删除确认", {
      type: "warning",
      confirmButtonText: "确定删除",
      cancelButtonText: "取消"
    })
      .then(async () => {
        await runBatch(rows, row => config.deleteApi!(row), "删除");
        clearSelection();
        fetchList();
      })
      .catch(() => {});
  };

  // ── 单条删除 ────────────────────────────────────────────
  const handleDelete = (row: T, name?: string) => {
    if (!config.deleteApi) return Promise.resolve(false);
    const text = (
      config.deleteConfirmText ?? "确定要删除「{name}」吗？"
    ).replace("{name}", name ?? rowDisplayName(row));
    return ElMessageBox.confirm(text, "删除确认", {
      type: "warning",
      confirmButtonText: "确定",
      cancelButtonText: "取消"
    })
      .then(async () => {
        try {
          const res: any = await config.deleteApi!(row);
          if (res && res.success === false) {
            ElMessage.error(res.message || "删除失败");
            return false;
          }
          ElMessage.success("删除成功");
          fetchList();
          return true;
        } catch (err: any) {
          const reason =
            err?.response?.data?.detail ||
            err?.response?.data?.message ||
            err?.message;
          ElMessage.error(
            typeof reason === "string" && reason
              ? `删除失败：${reason}`
              : "删除失败"
          );
          return false;
        }
      })
      .catch(() => false);
  };

  // ── Excel 导出（按当前筛选拉全量 / 仅选中行，按可见列生成）──
  const exporting = ref(false);
  const exportColumns = computed(() =>
    orderedColumns.value.filter(
      c =>
        c.exportable !== false &&
        (c.alwaysVisible || visibleColumns.value.includes(c.key))
    )
  );
  const exportHeaderOf = (c: TableColumnConfig<T>) => c.exportHeader ?? c.label;
  const exportValueOf = (c: TableColumnConfig<T>, row: T): string | number => {
    if (c.exportValue) return c.exportValue(row);
    if (c.formatter) return c.formatter(row);
    const v = c.prop ? row[c.prop] : undefined;
    return v == null ? "" : v;
  };

  const handleExport = async (source: "filter" | "selected" = "filter") => {
    exporting.value = true;
    try {
      let list: T[];
      let totalCount = 0;
      if (source === "selected") {
        list = [...selectedRows.value];
        totalCount = list.length;
        if (!totalCount) {
          ElMessage.warning("未选中任何行");
          return;
        }
      } else {
        const res = await fetchApi(filter.value, 1, exportBatchSize);
        list = res.list;
        totalCount = res.total;
      }
      if (!list.length) {
        ElMessage.warning("当前筛选条件下没有数据可导出");
        return;
      }

      const cols = exportColumns.value;
      const headers = cols.map(exportHeaderOf);
      const rows = list.map(r => cols.map(c => exportValueOf(c, r)));

      const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
      ws["!cols"] = headers.map(() => ({ wch: 18 }));
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(
        wb,
        ws,
        config.exportSheetName ?? config.title
      );
      const now = new Date();
      const pad = (n: number) => String(n).padStart(2, "0");
      const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`;
      const tag = source === "selected" ? "选中" : "筛选";
      XLSX.writeFile(
        wb,
        `${config.exportFileName ?? config.title}_${tag}_${ts}.xlsx`
      );

      ElMessage.success(
        `已导出 ${list.length} 条${source === "filter" && totalCount > exportBatchSize ? `（超 ${exportBatchSize} 条已截断）` : ""}`
      );
    } catch {
      ElMessage.error("导出失败");
    } finally {
      exporting.value = false;
    }
  };

  // ── Excel 导入（结果弹窗状态由基座组件渲染）──────────────
  const importLoading = ref(false);
  const importResultVisible = ref(false);
  const importResult = ref<any>(null);
  const handleImport = async (file: File) => {
    if (!config.importApi) return false;
    importLoading.value = true;
    importResultVisible.value = true;
    importResult.value = null;
    try {
      importResult.value = await config.importApi(file);
      fetchList();
      config.onImported?.();
    } catch (e: any) {
      importResult.value = {
        success: false,
        message: e?.response?.data?.detail || e?.message || "导入失败"
      };
    } finally {
      importLoading.value = false;
    }
    return false; // 阻止 el-upload 默认上传
  };

  return {
    // 数据
    loading,
    tableData,
    total,
    query,
    filter,
    fetchList,
    handleSearch,
    handleReset,
    handlePageChange,
    handleSizeChange,
    // 关键词
    keyword,
    handleKeywordSearch,
    // 表头筛选
    hf,
    // 列偏好
    columns,
    visibleColumns,
    columnOrder,
    orderedColumns,
    isKeyVisible,
    loadColumnPrefs,
    onColumnPrefsChange,
    onColumnOrderChange,
    selectAllColumns,
    clearAllColumns,
    invertColumns,
    panelColumnItems,
    panelGroups,
    toggleGroupColumns,
    // 拖拽
    tableRef,
    showTable,
    onTableMounted,
    // 多选
    selectedRows,
    handleSelectionChange,
    clearSelection,
    // 批量
    bulkEditVisible,
    bulkEditLoading,
    handleBulkEdit,
    handleBatchDelete,
    // 单删
    handleDelete,
    // 导出
    exporting,
    handleExport,
    // 导入
    importLoading,
    importResultVisible,
    importResult,
    handleImport
  };
}

export type TablePageStore<T = any> = ReturnType<typeof useTablePage<T>>;
