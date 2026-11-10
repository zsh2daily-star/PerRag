import { watch, nextTick, type Ref, type ComputedRef } from "vue";

/** 列定义（只需 key + label 用于拖拽时按表头文本识别列） */
interface DragColumnOption {
  key: string;
  label: string;
}

/**
 * 表头列拖拽排序（原生 HTML5 拖拽，按表头 .cell 文本匹配列 label）。
 *
 * 复用于企业页 / 厂中厂页等任何使用 el-table + columnOrder 的列表页。
 * 拖动表头某一列到另一列位置，自动重排 columnOrder 并触发持久化回调。
 *
 * @param tableRef      el-table 实例引用（ref）
 * @param columnOptions 列定义（含 key/label），用于按表头文本反查列 key
 * @param columnOrder   当前列顺序（双向绑定：拖拽后会被就地重排）
 * @param onOrderChange 拖拽完成后的回调（通常用于 localStorage 持久化）
 * @param showTable     可选：v-if 重建表格用的开关。传入后会在重建后自动重新绑定拖拽
 */
export function useColumnDrag(
  tableRef: Ref<any>,
  columnOptions: DragColumnOption[] | ComputedRef<DragColumnOption[]>,
  columnOrder: Ref<string[]>,
  onOrderChange?: () => void,
  showTable?: Ref<boolean>
) {
  let draggedColKey = "";

  const resolveOptions = (): DragColumnOption[] =>
    Array.isArray(columnOptions) ? columnOptions : columnOptions.value;

  /** 绑定表头 th 的拖拽事件 */
  const initHeaderDrag = () => {
    const theadTr = tableRef.value?.$el?.querySelector(
      ".el-table__header thead tr"
    ) as HTMLElement | null;
    if (!theadTr) return;

    const ths = Array.from(theadTr.children) as HTMLElement[];
    for (const th of ths) {
      // 清除旧事件，避免重复绑定
      th.ondragstart = null;
      th.ondragover = null;
      th.ondragenter = null;
      th.ondragleave = null;
      th.ondrop = null;

      // 按 .cell 内的文本匹配列标签（标签在 popover 内容之前，用 startsWith 匹配）
      const cellText = th.querySelector(".cell")?.textContent?.trim() || "";
      let key = "";
      for (const opt of resolveOptions()) {
        if (cellText.startsWith(opt.label)) {
          key = opt.key;
          break;
        }
      }
      if (!key) {
        th.removeAttribute("draggable");
        th.style.cursor = "";
        continue;
      }

      th.dataset.colKey = key;
      th.setAttribute("draggable", "true");
      th.style.cursor = "grab";

      th.ondragstart = (e: DragEvent) => {
        draggedColKey = key;
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", key);
        }
      };
      th.ondragover = (e: DragEvent) => {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      };
      th.ondragenter = (e: DragEvent) => {
        e.preventDefault();
        if (draggedColKey && draggedColKey !== key) {
          th.style.backgroundColor = "var(--el-table-row-hover-bg-color)";
        }
      };
      th.ondragleave = () => {
        th.style.backgroundColor = "";
      };
      th.ondrop = (e: DragEvent) => {
        e.preventDefault();
        th.style.backgroundColor = "";
        if (!draggedColKey || draggedColKey === key) return;

        const arr = [...columnOrder.value];
        const oldIdx = arr.indexOf(draggedColKey);
        const newIdx = arr.indexOf(key);
        if (oldIdx < 0 || newIdx < 0) return;
        const [moved] = arr.splice(oldIdx, 1);
        arr.splice(newIdx, 0, moved);
        columnOrder.value = arr;
        onOrderChange?.();

        // 重建表格以让新列顺序生效（若有 showTable 开关）
        if (showTable) {
          showTable.value = false;
          nextTick(() => {
            showTable.value = true;
          });
        }
      };
    }
  };

  // 表格重建后自动重新绑定拖拽（thead 渲染滞后于 nextTick，需延迟）
  if (showTable) {
    watch(showTable, val => {
      if (val) setTimeout(initHeaderDrag, 50);
    });
  }

  /** el-table 挂载后初始化拖拽（含 v-if 重建场景，thead 渲染可能滞后） */
  const onTableMounted = () => {
    setTimeout(initHeaderDrag, 50);
  };

  return { initHeaderDrag, onTableMounted };
}
