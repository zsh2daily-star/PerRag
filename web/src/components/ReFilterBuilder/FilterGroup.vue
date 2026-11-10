<script setup lang="ts">
import { computed } from "vue";
import { Plus, Delete } from "@element-plus/icons-vue";
import type {
  FilterFieldSpec,
  FilterGroup as FilterGroupT,
  FilterLeaf as FilterLeafT,
  FilterNode,
  Operator
} from "./types";
import { makeLeaf, makeGroup } from "./types";
import FilterLeaf from "./FilterLeaf.vue";

defineOptions({ name: "FilterGroup" });

const props = defineProps<{
  modelValue: FilterGroupT;
  fields: FilterFieldSpec[];
  depth: number;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: FilterGroupT): void;
  (e: "remove"): void;
}>();

const MAX_DEPTH = 2;
const canAddSubgroup = computed(() => props.depth < MAX_DEPTH - 1);

const patch = (p: Partial<FilterGroupT>) => {
  emit("update:modelValue", { ...props.modelValue, ...p });
};

const handleLogicChange = (logic: "AND" | "OR") => {
  patch({ logic });
};

const updateChild = (idx: number, newNode: FilterNode) => {
  const nextChildren = props.modelValue.children.slice();
  nextChildren[idx] = newNode;
  patch({ children: nextChildren });
};

const addLeaf = () => {
  if (props.fields.length === 0) return;
  const firstField = props.fields[0];
  const newLeaf: FilterLeafT = makeLeaf(firstField.key, firstField.type);
  if (firstField.operators.length > 0) {
    newLeaf.operator = firstField.operators[0] as Operator;
  }
  patch({ children: [...props.modelValue.children, newLeaf] });
};

const addSubgroup = () => {
  if (!canAddSubgroup.value) return;
  const newGroup = makeGroup("AND");
  patch({ children: [...props.modelValue.children, newGroup] });
};

const removeChild = (idx: number) => {
  const nextChildren = props.modelValue.children.slice();
  nextChildren.splice(idx, 1);
  patch({ children: nextChildren });
};
</script>

<template>
  <!-- 根组（depth=0）：纯横向 flex-wrap，无边框无 padding -->
  <div v-if="depth === 0" class="filter-group root">
    <!-- 子组（depth>=1）的连接词先渲染：idx>=1 时出现 -->
    <template v-for="(child, idx) in modelValue.children" :key="idx">
      <span v-if="idx >= 1" class="connector">
        <el-select
          :model-value="modelValue.logic"
          size="small"
          style="width: 100px"
          @change="handleLogicChange"
        >
          <el-option label="并且" value="AND" />
          <el-option label="或者" value="OR" />
        </el-select>
      </span>

      <FilterLeaf
        v-if="child.type === 'leaf'"
        :model-value="child"
        :fields="fields"
        @update:model-value="updateChild(idx, $event)"
        @remove="removeChild(idx)"
      />

      <FilterGroup
        v-else-if="child.type === 'group'"
        :model-value="child"
        :fields="fields"
        :depth="1"
        @update:model-value="updateChild(idx, $event)"
        @remove="removeChild(idx)"
      />
    </template>

    <!-- 添加按钮（根组：永远在末尾横排） -->
    <div class="add-bar">
      <el-button
        :icon="Plus"
        size="default"
        @click="addLeaf"
      >
        添加条件
      </el-button>
      <el-button
        v-if="canAddSubgroup"
        :icon="Plus"
        size="default"
        @click="addSubgroup"
      >
        添加组
      </el-button>
    </div>
  </div>

  <!-- 子组（depth>=1）：括号式包裹，浅灰圆角背景 -->
  <div v-else class="filter-group subgroup">
    <!-- 子组标签 + 删除 -->
    <div class="subgroup-tag">
      <span class="logic-badge">{{ modelValue.logic === "AND" ? "全部满足" : "任一满足" }}</span>
      <el-button
        :icon="Delete"
        circle
        size="small"
        @click="emit('remove')"
      />
    </div>

    <!-- children -->
    <div class="subgroup-body">
      <template v-for="(child, idx) in modelValue.children" :key="idx">
        <span v-if="idx >= 1" class="connector">
          <el-select
            :model-value="modelValue.logic"
            size="small"
            style="width: 100px"
            @change="handleLogicChange"
          >
            <el-option label="并且" value="AND" />
            <el-option label="或者" value="OR" />
          </el-select>
        </span>

        <FilterLeaf
          v-if="child.type === 'leaf'"
          :model-value="child"
          :fields="fields"
          @update:model-value="updateChild(idx, $event)"
          @remove="removeChild(idx)"
        />

        <!-- 再深度嵌套（depth=2，兜底）直接也当 subgroup 渲染，不显示添加按钮 -->
        <FilterGroup
          v-else-if="child.type === 'group'"
          :model-value="child"
          :fields="fields"
          :depth="depth + 1"
          @update:model-value="updateChild(idx, $event)"
          @remove="removeChild(idx)"
        />
      </template>

      <!-- 子组内的添加按钮 -->
      <div class="add-bar">
        <el-button
          :icon="Plus"
          size="small"
          text
          @click="addLeaf"
        >
          添加
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── 根组（depth=0）：横向 flex-wrap ── */
.filter-group.root {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0;
}
.root > * {
  margin-right: 8px;
}

/* ── 连接词 ── */
.connector {
  display: inline-flex;
  align-items: center;
  margin-right: 8px;
}

/* ── 添加按钮区 ── */
.add-bar {
  display: inline-flex;
  gap: 6px;
  margin-right: 8px;
}

/* ── 子组（depth>=1）：括号式 ── */
.filter-group.subgroup {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: flex-start;
  background: var(--el-fill-color-light);
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
  padding: 6px 8px;
  margin-right: 8px;
}
.subgroup-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 6px;
  flex-shrink: 0;
}
.logic-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 6px;
  border-radius: 3px;
}
.subgroup-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0;
}
.subgroup-body > * {
  margin-right: 6px;
}
</style>
