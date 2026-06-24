<!-- 任务进度：订阅 SSE 事件，实时展示总进度与逐探针状态网格。 -->
<template>
  <div class="task-progress">
    <div class="progress-head">
      <span class="muted">{{ phaseText }}</span>
      <span class="muted">{{ doneCount }}/{{ totalProbes || '—' }}</span>
    </div>
    <n-progress
      type="line"
      :percentage="percentage"
      :status="progressStatus"
      :indicator-placement="'inside'"
    />

    <div v-if="probes.length" class="probe-grid">
      <n-tooltip v-for="probe in probes" :key="probe.strategy_key" trigger="hover">
        <template #trigger>
          <span class="probe-dot" :class="dotClass(probe.status)">
            {{ probe.strategy_key }}
          </span>
        </template>
        {{ categoryLabel(probe.category) }} · {{ statusText(probe.status) }}
      </n-tooltip>
    </div>

    <p v-if="scoreValue !== null" class="score-line">
      综合得分：<strong>{{ scoreValue.toFixed(1) }}</strong>
    </p>
    <p v-if="failReason" class="muted fail-line">原因：{{ failReason }}</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { subscribeTaskEvents } from '../utils/sse_client'
import { STATUS_LABELS } from '../utils/format'

const props = defineProps({
  taskId: { type: Number, required: true }
})
const emit = defineEmits(['done'])

const totalProbes = ref(0)
const probes = ref([])
const percentage = ref(0)
const phase = ref('running')
const scoreValue = ref(null)
const failReason = ref('')
let unsubscribe = null

const CATEGORY_LABELS = {
  connectivity: '连通性',
  performance: '性能',
  billing: '计费一致性',
  capability: '能力',
  authenticity: '真实性'
}

const doneCount = computed(() => probes.value.length)

const progressStatus = computed(() => {
  if (phase.value === 'failed') return 'error'
  if (phase.value === 'completed') return 'success'
  return 'default'
})

const phaseText = computed(() => {
  const map = {
    running: '检测进行中…',
    scoring: '评分中…',
    completed: '检测完成',
    failed: '检测失败',
    canceled: '已取消'
  }
  return map[phase.value] || '准备中…'
})

function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category
}
function statusText(status) {
  return STATUS_LABELS[status]?.text || status
}
function dotClass(status) {
  return `dot-${status}`
}

function reset() {
  totalProbes.value = 0
  probes.value = []
  percentage.value = 0
  phase.value = 'running'
  scoreValue.value = null
  failReason.value = ''
}

function start() {
  unsubscribe?.()
  reset()
  unsubscribe = subscribeTaskEvents(props.taskId, {
    onEvent: (type, data) => {
      if (type === 'task.started') {
        totalProbes.value = data.total_probes || 0
      } else if (type === 'probe.completed') {
        probes.value.push(data)
        percentage.value = Math.round((data.progress || 0) * 100)
      } else if (type === 'task.scored') {
        phase.value = 'scoring'
        scoreValue.value = typeof data.score === 'number' ? data.score : null
      }
    },
    onDone: (type, data) => {
      phase.value = type.split('.')[1] // completed | failed | canceled
      percentage.value = Math.round((data.progress ?? 1) * 100)
      if (data.reason) failReason.value = data.reason
      emit('done', { taskId: props.taskId, phase: phase.value })
    },
    onError: () => {
      // 连接中断：保留已有进度，标记失败便于用户重试查看。
      if (phase.value === 'running' || phase.value === 'scoring') {
        failReason.value = '事件流连接中断'
      }
    }
  })
}

watch(() => props.taskId, start, { immediate: true })
onBeforeUnmount(() => unsubscribe?.())
</script>

<style scoped>
.progress-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}
.probe-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
}
.probe-dot {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #eef1f5;
  color: #5b6b7c;
  cursor: default;
}
.dot-pass {
  background: #e3f6ea;
  color: #18794e;
}
.dot-degraded {
  background: #fdf2dc;
  color: #ad6800;
}
.dot-fail {
  background: #fde4e4;
  color: #b42318;
}
.dot-skipped {
  background: #eef1f5;
  color: #8a96a3;
}
.score-line {
  margin-top: 14px;
  font-size: 14px;
}
.fail-line {
  margin-top: 6px;
}
</style>
