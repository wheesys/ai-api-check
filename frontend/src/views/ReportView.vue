<!-- 检测报告视图：综合画像 + 真实性专章 + 维度图表 + 策略下钻 + PDF 导出。 -->
<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">检测报告 · 任务 #{{ taskId }}</h1>
        <p class="muted">三层结果装配：汇总得分、真实性研判、策略明细与探针下钻。</p>
      </div>
      <n-space>
        <n-button quaternary @click="goBack">返回任务</n-button>
        <n-button type="primary" :loading="exporting" :disabled="!result" @click="exportPdf">
          导出 PDF
        </n-button>
      </n-space>
    </div>

    <n-spin :show="loading">
      <n-result
        v-if="loadError"
        status="warning"
        title="报告暂不可用"
        :description="loadError"
      />

      <template v-else-if="result">
        <!-- 综合得分卡片 -->
        <div class="score-grid">
          <n-card v-for="card in scoreCards" :key="card.key" size="small">
            <div class="score-card">
              <span class="muted">{{ card.label }}</span>
              <span class="score-value" :class="card.cls">{{ card.value }}</span>
            </div>
          </n-card>
        </div>

        <!-- 真实性研判专章 -->
        <n-card v-if="subscores" class="section-card" title="真实性研判" size="small">
          <n-space align="center" :size="16" class="auth-head">
            <n-tag :type="authLevel.type" size="large" :bordered="false">
              {{ authLevel.text }}
            </n-tag>
            <span class="muted">
              置信度 {{ formatScore(subscores.confidence) }} ·
              阈值 H{{ subscores.high_threshold }}/L{{ subscores.low_threshold }} ·
              综合 {{ formatScore(subscores.authenticity_score) }}（取套壳/直供短板）
            </span>
          </n-space>
          <n-grid :cols="2" :x-gap="16" responsive="screen" item-responsive>
            <n-gi span="2 m:1">
              <base-chart ref="overallChartRef" :option="overallRadarOption" />
            </n-gi>
            <n-gi span="2 m:1">
              <base-chart ref="authChartRef" :option="authBarOption" />
            </n-gi>
          </n-grid>

          <n-data-table
            v-if="signals.length"
            size="small"
            :columns="signalColumns"
            :data="signals"
            :bordered="false"
            :pagination="{ pageSize: 6 }"
          />
        </n-card>

        <!-- 性能趋势 -->
        <n-card
          v-if="performanceOption"
          class="section-card"
          title="性能指标"
          size="small"
        >
          <base-chart ref="perfChartRef" :option="performanceOption" />
        </n-card>

        <!-- 策略明细（按类别分组，可下钻探针） -->
        <n-card class="section-card" title="策略检测明细" size="small">
          <n-data-table
            :columns="strategyColumns"
            :data="strategies"
            :row-key="(row) => row.id"
            :pagination="{ pageSize: 12 }"
          />
        </n-card>
      </template>
    </n-spin>

    <!-- 探针下钻抽屉 -->
    <n-drawer v-model:show="showProbes" :width="560">
      <n-drawer-content :title="`探针记录 · ${activeStrategyName}`" closable>
        <n-spin :show="probeLoading">
          <n-empty v-if="!probeRecords.length" description="无探针记录" />
          <n-list v-else>
            <n-list-item v-for="probe in probeRecords" :key="probe.id">
              <n-thing :title="probe.probe_type">
                <template #description>
                  <n-space :size="6">
                    <n-tag size="small" :type="probe.success ? 'success' : 'error'" :bordered="false">
                      {{ probe.success ? '成功' : '失败' }}
                    </n-tag>
                    <n-tag v-if="probe.http_status" size="small" :bordered="false">
                      HTTP {{ probe.http_status }}
                    </n-tag>
                    <n-tag v-if="probe.ttft_ms" size="small" :bordered="false">
                      TTFT {{ probe.ttft_ms }}ms
                    </n-tag>
                  </n-space>
                </template>
                <p v-if="probe.error_message" class="muted">{{ probe.error_message }}</p>
              </n-thing>
            </n-list-item>
          </n-list>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { reportsApi } from '../api/reports'
import {
  AUTH_LEVEL_LABELS,
  STATUS_LABELS,
  formatScore,
  downloadBlob
} from '../utils/format'
import {
  buildOverallRadar,
  buildAuthenticityBar,
  buildPerformanceChart
} from '../utils/chart_config'
import BaseChart from '../components/BaseChart.vue'

const props = defineProps({
  taskId: { type: String, required: true }
})

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const loadError = ref('')
const exporting = ref(false)
const result = ref(null)
const strategies = ref([])

const overallChartRef = ref(null)
const authChartRef = ref(null)
const perfChartRef = ref(null)

// 探针下钻
const showProbes = ref(false)
const probeLoading = ref(false)
const probeRecords = ref([])
const activeStrategyName = ref('')

const taskIdNum = computed(() => Number(props.taskId))

// ---- 解析真实性子分 ----
const subscores = computed(() => {
  const raw = result.value?.authenticity_subscores_json
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
})

const authLevel = computed(() => {
  const level = subscores.value?.level
  return AUTH_LEVEL_LABELS[level] || { text: '未知', type: 'default' }
})

const signals = computed(() => subscores.value?.signals || [])

// ---- 得分卡片 ----
const scoreCards = computed(() => {
  const r = result.value
  if (!r) return []
  return [
    { key: 'overall', label: '综合得分', value: formatScore(r.overall_score), cls: 'primary' },
    { key: 'connectivity', label: '连通性', value: formatScore(r.connectivity_score) },
    { key: 'performance', label: '性能', value: formatScore(r.performance_score) },
    { key: 'billing', label: '计费一致性', value: formatScore(r.billing_score) },
    { key: 'capability', label: '能力', value: formatScore(r.capability_score) },
    { key: 'authenticity', label: '真实性', value: formatScore(r.authenticity_score) }
  ]
})

// ---- 图表 option ----
const overallRadarOption = computed(() => buildOverallRadar(result.value))
const authBarOption = computed(() => buildAuthenticityBar(subscores.value))
const performanceOption = computed(() => {
  const perf = strategies.value.filter((s) => s.strategy_category === 'performance')
  return buildPerformanceChart(perf)
})

// ---- 信号表 ----
const signalColumns = [
  { title: '信号', key: 'key', ellipsis: { tooltip: true } },
  {
    title: '方向',
    key: 'direction',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { size: 'small', type: row.direction === 'confirm' ? 'success' : 'error', bordered: false },
        { default: () => (row.direction === 'confirm' ? '证真' : '证伪') }
      )
  },
  {
    title: '贡献',
    key: 'contribution',
    width: 90,
    render: (row) => formatScore(row.contribution)
  },
  { title: '证据', key: 'evidence', ellipsis: { tooltip: true } }
]

// ---- 策略表 ----
const CATEGORY_LABELS = {
  connectivity: '连通性',
  performance: '性能',
  billing: '计费一致性',
  capability: '能力',
  authenticity: '真实性'
}

const strategyColumns = [
  {
    title: '类别',
    key: 'strategy_category',
    width: 110,
    render: (row) => CATEGORY_LABELS[row.strategy_category] || row.strategy_category
  },
  { title: '策略', key: 'strategy_name', ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'result_status',
    width: 100,
    render: (row) => {
      const meta = STATUS_LABELS[row.result_status] || { text: row.result_status, type: 'default' }
      return h(NTag, { size: 'small', type: meta.type, bordered: false }, { default: () => meta.text })
    }
  },
  {
    title: '得分',
    key: 'score',
    width: 80,
    render: (row) => formatScore(row.score)
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', tertiary: true, onClick: () => openProbes(row) },
        { default: () => '探针' }
      )
  }
]

async function openProbes(strategy) {
  activeStrategyName.value = strategy.strategy_name
  showProbes.value = true
  probeLoading.value = true
  probeRecords.value = []
  try {
    probeRecords.value = await reportsApi.probes(taskIdNum.value, strategy.id)
  } catch (error) {
    message.error(error.message)
  } finally {
    probeLoading.value = false
  }
}

function goBack() {
  router.push({ name: 'tasks' })
}

// 收集已渲染图表为 data-uri，POST 导出 PDF。
async function exportPdf() {
  exporting.value = true
  try {
    const charts = {}
    const radar = overallChartRef.value?.getDataURL?.()
    const auth = authChartRef.value?.getDataURL?.()
    const perf = perfChartRef.value?.getDataURL?.()
    if (radar) charts.overall_radar = radar
    if (auth) charts.authenticity_bar = auth
    if (perf) charts.performance = perf
    const blob = await reportsApi.exportPdf(taskIdNum.value, charts)
    downloadBlob(blob, `report-task-${props.taskId}.pdf`)
    message.success('PDF 已导出')
  } catch (error) {
    message.error(error.message)
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const [resultData, strategyData] = await Promise.all([
      reportsApi.result(taskIdNum.value),
      reportsApi.strategies(taskIdNum.value)
    ])
    result.value = resultData
    strategies.value = strategyData
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.section-card {
  margin-top: 16px;
}
.score-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.score-value {
  font-size: 24px;
  font-weight: 600;
}
.score-value.primary {
  color: #2f6fed;
}
.auth-head {
  margin-bottom: 12px;
}
</style>
