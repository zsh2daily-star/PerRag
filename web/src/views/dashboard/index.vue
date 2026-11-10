<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { getDashboardStats } from "@/api/dashboard";
import { fmtDate } from "@/utils/date";

defineOptions({ name: "Dashboard" });

interface OverdueItem {
  id: number;
  title: string;
  enterprise: string;
  deadline: string;
  overdue_days: number;
}

interface RecentMaterial {
  id: number;
  filename: string;
  format: string;
  status: "pending" | "indexing" | "indexed" | "failed";
  uploaded_at: string;
}

interface DashboardStats {
  unclosed: number;
  overdue: number;
  month_new: number;
  material_total: number;
  level_distribution: {
    low: number;
    medium: number;
    high: number;
  };
  overdue_list: OverdueItem[];
  recent_materials: RecentMaterial[];
}

const loading = ref(false);
const stats = reactive<DashboardStats>({
  unclosed: 0,
  overdue: 0,
  month_new: 0,
  material_total: 0,
  level_distribution: { low: 0, medium: 0, high: 0 },
  overdue_list: [],
  recent_materials: []
});

const statusTagType: Record<string, string> = {
  pending: "info",
  indexing: "",
  indexed: "success",
  failed: "danger"
};

const statusText: Record<string, string> = {
  pending: "待处理",
  indexing: "索引中",
  indexed: "已索引",
  failed: "失败"
};

const levelColorMap: Record<string, string> = {
  low: "#67c23a",
  medium: "#e6a23c",
  high: "#f56c6c"
};

const fetchStats = async () => {
  loading.value = true;
  try {
    const data = await getDashboardStats();
    Object.assign(stats, data);
  } catch {
    ElMessage.error("加载仪表盘数据失败");
  } finally {
    loading.value = false;
  }
};

const totalLevel = () => {
  const { low, medium, high } = stats.level_distribution;
  const sum = low + medium + high;
  return sum === 0 ? 1 : sum;
};

onMounted(() => {
  fetchStats();
});
</script>

<template>
  <div v-loading="loading" class="p-4">
    <!-- 统计卡片 -->
    <el-row :gutter="16" class="mb-4">
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover">
          <el-statistic title="未闭环数" :value="stats.unclosed" />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover">
          <el-statistic
            title="超期数"
            :value="stats.overdue"
            :value-style="{ color: '#f56c6c' }"
          />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover">
          <el-statistic title="本月新增" :value="stats.month_new" />
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover">
          <el-statistic title="材料总数" :value="stats.material_total" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 隐患等级分布 -->
      <el-col :xs="24" :md="12" class="mb-4">
        <el-card shadow="hover">
          <template #header>
            <span class="font-bold">隐患等级分布</span>
          </template>
          <div class="space-y-4">
            <div>
              <div class="flex justify-between mb-1">
                <span>低危</span>
                <span>{{ stats.level_distribution.low }}</span>
              </div>
              <el-progress
                :percentage="
                  Math.round(
                    (stats.level_distribution.low / totalLevel()) * 100
                  )
                "
                :color="levelColorMap.low"
              />
            </div>
            <div>
              <div class="flex justify-between mb-1">
                <span>中危</span>
                <span>{{ stats.level_distribution.medium }}</span>
              </div>
              <el-progress
                :percentage="
                  Math.round(
                    (stats.level_distribution.medium / totalLevel()) * 100
                  )
                "
                :color="levelColorMap.medium"
              />
            </div>
            <div>
              <div class="flex justify-between mb-1">
                <span>高危</span>
                <span>{{ stats.level_distribution.high }}</span>
              </div>
              <el-progress
                :percentage="
                  Math.round(
                    (stats.level_distribution.high / totalLevel()) * 100
                  )
                "
                :color="levelColorMap.high"
              />
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- 超期待办列表 -->
      <el-col :xs="24" :md="12" class="mb-4">
        <el-card shadow="hover">
          <template #header>
            <span class="font-bold">超期待办（Top 5）</span>
          </template>
          <el-table :data="stats.overdue_list" size="small" border>
            <el-table-column prop="title" label="标题" min-width="120" />
            <el-table-column
              prop="enterprise"
              label="企业"
              min-width="100"
            />
            <el-table-column label="超期天数" width="90" align="center">
              <template #default="{ row }">
                <el-tag type="danger" size="small">
                  {{ row.overdue_days }}天
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近材料状态 -->
    <el-card shadow="hover">
      <template #header>
        <span class="font-bold">最近材料状态</span>
      </template>
      <el-table :data="stats.recent_materials" size="small" border>
        <el-table-column prop="filename" label="文件名" min-width="180" />
        <el-table-column prop="format" label="格式" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small">{{ row.format }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType[row.status]" size="small">
              {{ statusText[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="uploaded_at"
          label="上传时间"
          width="120"
          align="center"
        >
          <template #default="{ row }">{{ fmtDate(row.uploaded_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
