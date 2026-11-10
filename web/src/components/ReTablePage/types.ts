/**
 * 统一表格基座（ReTablePage）配置类型定义
 *
 * 目标：所有表格页共享同一套「加载 / 筛选 / 列偏好 / 导入导出 / 批量修改」逻辑，
 * 页面差异全部通过 TablePageConfig 声明，特殊单元格/操作列经具名插槽注入。
 *
 * 筛选协议与后端 app/services/filter_service.py 一致（FilterGroup/FilterLeaf）。
 */
import type { FilterGroup } from "@/components/ReFilterBuilder/types";
import type { BulkEditField } from "@/components/ReBulkEdit/index.vue";
import type { HeaderFilterHandlers } from "@/composables/useHeaderFilters";

/** 表头筛选 UI 类型（与 ReFilterHeader 的 type 一一对应） */
export type ColFilterType = "text" | "number" | "date" | "enum" | "bool";

/** 列显示面板分组信息 */
export interface ColumnGroupDef {
  /** 分组名（显示在列设置面板） */
  name: string;
  /** 属于该组的列 key（顺序即面板内顺序） */
  keys: string[];
}

/** 单列表格配置 */
export interface TableColumnConfig<T = any> {
  /** 列唯一 key（列偏好/排序/显隐均以此为准） */
  key: string;
  /** 行数据字段名（默认取 row[prop]；使用插槽列时可省略） */
  prop?: string;
  /** 表头显示名 */
  label: string;
  /** 固定列宽 */
  width?: number;
  /** 最小列宽 */
  minWidth?: number;
  /** 对齐方式 */
  align?: "left" | "center" | "right";
  /** 固定到左侧/右侧 */
  fixed?: "left" | "right";
  /** 超长省略号悬浮提示 */
  showOverflowTooltip?: boolean;

  // ── 表头筛选（可选，不配则该列无筛选图标）──
  /** 后端筛选字段名（默认 = prop） */
  filterField?: string;
  /** 筛选 UI 类型 */
  filterType?: ColFilterType;
  /** enum 筛选选项（字符串数组或 {value,label}；传函数支持动态选项，如区域列表） */
  filterOptions?:
    | (string | { value: string; label: string })[]
    | (() => (string | { value: string; label: string })[]);
  /** 筛选输入框占位（text 类型） */
  filterPlaceholder?: string;
  /** enum 筛选是否带「（空）」选项，默认带 */
  filterNullOption?: boolean;
  /** enum 筛选「（空）」的显示文案 */
  filterNullLabel?: string;
  /** 数组枚举列（text[] 多标签）：多选用 overlaps 而非 in（如 enterprise_category） */
  filterArrayEnum?: boolean;

  // ── 单元格渲染 ──
  /** 纯文本格式化（返回字符串；设置了 slot 时忽略） */
  formatter?: (row: T) => string | number;
  /**
   * 自定义单元格插槽名：父页面用 <template #cell-xxx="{ row }"> 注入。
   * 设置后该列内容完全由插槽渲染。
   */
  slot?: string;
  /**
   * 自定义表头插槽名（如组合筛选列）：父页面用 <template #header-xxx="{ column }"> 注入。
   * 优先级高于 filterType 自动渲染的 ReFilterHeader。
   */
  headerSlot?: string;
  /**
   * 内联可编辑：设为后端字段名后，该列自动渲染 EditableCell（点击文本→输入框→Enter 确认弹窗→保存）。
   * 需要 TablePageConfig.inlineUpdateApi 配合。
   * 有 slot 时优先 slot（不自动渲染 EditableCell）。
   */
  editable?: string;

  // ── 导出 ──
  /** 导出 Excel 表头名（默认 = label） */
  exportHeader?: string;
  /** 导出值提取（默认 = formatter ?? row[prop]） */
  exportValue?: (row: T) => string | number;
  /** false 时该列不参与导出（默认参与） */
  exportable?: boolean;

  // ── 其他 ──
  /** 该列不在「列显示」面板出现（始终显示），如名称列 */
  alwaysVisible?: boolean;
}

/** 统一查询接口签名（POST /{table}/query 协议） */
export type TableFetchApi<T = any> = (
  filter: FilterGroup,
  page: number,
  pageSize: number
) => Promise<{ list: T[]; total: number }>;

/** 统一表格页配置 */
export interface TablePageConfig<T = any> {
  /** 列偏好 localStorage 键前缀（实际键为 table-cols:{storageKey} / table-cols:{storageKey}:order） */
  storageKey: string;
  /** 表格标题（卡片左上角） */
  title: string;
  /** 统一查询接口 */
  fetchApi: TableFetchApi<T>;
  /** 列配置 */
  columns: TableColumnConfig<T>[];
  /** 默认可见列 key（未命中 localStorage 时用；默认 = 全部列） */
  defaultVisible?: string[];
  /** 列显示面板分组（不配 = 平铺） */
  columnGroups?: ColumnGroupDef[];
  /** 旧版列偏好迁移钩子（如组合列拆分） */
  migrateColumnPrefs?: (saved: string[]) => string[];
  /** 旧版列偏好 localStorage 键（一次性迁移到 table-cols:{storageKey} 新键后删除旧键） */
  legacyColumnKeys?: { visible?: string; order?: string };

  // ── el-table 透传（可选）──
  /** 行合并（透传 el-table span-method，如按批次合并企业列） */
  spanMethod?: (params: {
    row: T;
    column: any;
    rowIndex: number;
    columnIndex: number;
  }) => { rowspan: number; colspan: number } | undefined;
  /** 行 class（透传 el-table row-class-name，如超期行标红） */
  rowClassName?: (params: { row: T; rowIndex: number }) => string;
  /** 每次列表加载完成钩子（页面构建行合并映射等） */
  onRowsLoaded?: (rows: T[]) => void;
  /** 行数据变换（加载后、渲染前应用，如按出租方稳定重排序以配合行合并） */
  transformRows?: (rows: T[]) => T[];
  /** 导入成功后的附加钩子（如刷新未关联计数） */
  onImported?: () => void;
  /** 导入结果弹窗宽度（默认 520） */
  importResultWidth?: number;

  /** 顶部关键词搜索占位；不配则隐藏关键词输入框 */
  keywordPlaceholder?: string;
  /** 预设筛选（如危化品页只看 has_hazardous_chemicals），不可被重置清除 */
  presetFilter?: FilterGroup;

  // ── 批量修改（可选）──
  /** 可批量修改字段（直接声明，页面自行映射 label/options） */
  bulkEditFields?: BulkEditField[];
  /** 批量修改逐条更新接口（返回原始响应即可，内部统一汇总成败） */
  bulkUpdateApi?: (row: T, payload: Record<string, any>) => Promise<unknown>;

  // ── 内联编辑（可选）──
  /**
   * 内联单元格编辑保存接口。
   * col.editable 声明哪些列可编辑（值 = 后端字段名），
   * 此函数接收 (row, field, value)，页面在此调 API 并更新行数据。
   */
  inlineUpdateApi?: (
    row: T,
    field: string,
    value: string
  ) => Promise<void>;

  // ── 批量删除（可选）──
  /** 单条删除接口 */
  deleteApi?: (row: T) => Promise<unknown>;
  /** 批量删除确认文案，{n} 替换为条数 */
  batchDeleteConfirmText?: string;
  /** 单条删除确认文案，{name} 替换为行显示名 */
  deleteConfirmText?: string;
  /** 行显示名提取（失败汇总用，默认 row.name ?? row.id） */
  rowDisplayName?: (row: T) => string;

  // ── Excel 导入（可选）──
  importApi?: (file: File) => Promise<unknown>;
  /** 导入按钮文案，默认「导入Excel」 */
  importText?: string;

  // ── Excel 导出（可选）──
  /** 导出文件名前缀（默认 = title） */
  exportFileName?: string;
  /** 导出工作表名（默认 = title） */
  exportSheetName?: string;

  /** 行 key（默认 "id"） */
  rowKey?: string;
  /** 导出/拉全量时单批大小（默认 5000） */
  exportBatchSize?: number;
  /** 重置筛选时的附加钩子（页面自有筛选状态清理用，如组合列） */
  onReset?: () => void;
}

/** 基座暴露给父页面的状态与方法（defineExpose / 插槽共用） */
export interface TablePageExpose<T = any> {
  /** 重新加载当前页 */
  reload: () => Promise<void>;
  /** 回到第一页并加载 */
  refresh: () => void;
  /** 当前页数据 */
  rows: import("vue").Ref<T[]>;
  /** 总条数 */
  total: import("vue").Ref<number>;
  /** 加载中 */
  loading: import("vue").Ref<boolean>;
  /** 当前选中行 */
  selectedRows: import("vue").Ref<T[]>;
  /** 筛选条件根组（高级筛选/预设筛选可读写） */
  filter: import("vue").Ref<FilterGroup>;
  /** 表头筛选处理器（特殊组合列可复用） */
  hf: HeaderFilterHandlers;
  /** 清空多选 */
  clearSelection: () => void;
  /** 触发导出 */
  runExport: (source: "filter" | "selected") => Promise<void>;
}
