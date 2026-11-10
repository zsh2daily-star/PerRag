/**
 * 表格列显隐 + 顺序偏好 composable —— 企业列表 / 巡查台账共用。
 *
 * 偏好持久化在 localStorage（各页面传不同 storageKey 互不污染）。
 * 历史版本列拆分等迁移通过 migrate 钩子处理。
 */
import { computed, ref } from "vue";

export interface ColumnPrefsOptions {
  /** 全部可用列 key（顺序即默认列顺序） */
  allKeys: string[];
  /** 默认可见列 key（未命中 localStorage 时使用，默认全部可见） */
  defaultVisible?: string[];
  /** 列可见偏好 localStorage 键 */
  storageKey: string;
  /** 列顺序偏好 localStorage 键（可选，不传则不持久化顺序） */
  orderStorageKey?: string;
  /** 历史偏好迁移钩子（如旧组合列拆分为新列） */
  migrate?: (saved: string[]) => string[];
}

export function useColumnPrefs(opts: ColumnPrefsOptions) {
  const { allKeys, storageKey, orderStorageKey, migrate } = opts;
  const defaultVisible = opts.defaultVisible ?? [...allKeys];

  const visibleColumns = ref<string[]>([...defaultVisible]);
  const columnOrder = ref<string[]>([...allKeys]);

  const availableColumnKeys = computed(() => allKeys);

  /** 按持久化顺序排列、且仅包含当前可用列的 key 列表（缺失列补到末尾） */
  const orderedKeys = computed(() => {
    const valid = columnOrder.value.filter(k => allKeys.includes(k));
    const missing = allKeys.filter(k => !valid.includes(k));
    return [...valid, ...missing];
  });

  const isKeyVisible = (key: string) => visibleColumns.value.includes(key);

  const loadColumnPrefs = () => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        let saved: string[] = JSON.parse(raw);
        if (Array.isArray(saved)) {
          if (migrate) saved = migrate(saved);
          visibleColumns.value = Array.from(
            new Set(saved.filter(k => allKeys.includes(k)))
          );
        }
      }
      if (orderStorageKey) {
        const orderRaw = localStorage.getItem(orderStorageKey);
        if (orderRaw) {
          const order = JSON.parse(orderRaw);
          if (Array.isArray(order)) {
            const valid = order.filter((k: string) => allKeys.includes(k));
            const missing = allKeys.filter(k => !valid.includes(k));
            columnOrder.value = [...valid, ...missing];
          }
        }
      }
    } catch {
      // 配置损坏时保持默认
    }
  };

  const onColumnPrefsChange = () => {
    localStorage.setItem(storageKey, JSON.stringify(visibleColumns.value));
  };

  const onColumnOrderChange = () => {
    if (orderStorageKey) {
      localStorage.setItem(orderStorageKey, JSON.stringify(columnOrder.value));
    }
  };

  const selectAllColumns = () => {
    visibleColumns.value = [...allKeys];
    onColumnPrefsChange();
  };
  const clearAllColumns = () => {
    visibleColumns.value = [];
    onColumnPrefsChange();
  };
  const invertColumns = () => {
    visibleColumns.value = allKeys.filter(k => !visibleColumns.value.includes(k));
    onColumnPrefsChange();
  };

  /** 组全选/清空（keys 为该组全部列） */
  const isGroupFullySelected = (keys: string[]) =>
    keys.length > 0 && keys.every(k => visibleColumns.value.includes(k));
  const toggleGroupColumns = (keys: string[]) => {
    if (isGroupFullySelected(keys)) {
      visibleColumns.value = visibleColumns.value.filter(k => !keys.includes(k));
    } else {
      const merged = new Set([...visibleColumns.value, ...keys]);
      visibleColumns.value = allKeys.filter(k => merged.has(k));
    }
    onColumnPrefsChange();
  };

  return {
    visibleColumns,
    columnOrder,
    availableColumnKeys,
    orderedKeys,
    isKeyVisible,
    loadColumnPrefs,
    onColumnPrefsChange,
    onColumnOrderChange,
    selectAllColumns,
    clearAllColumns,
    invertColumns,
    isGroupFullySelected,
    toggleGroupColumns
  };
}
