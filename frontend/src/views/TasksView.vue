<!-- 检测任务视图：任务列表 + 新建任务 + 实时进度 + 跳转报告。 -->
<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">检测任务</h1>
        <p class="muted">选择中转站与模型发起质量检测，实时跟踪进度并查看报告。</p>
      </div>
      <n-space>
        <n-button quaternary :loading="loading" @click="loadTasks">刷新</n-button>
        <n-button type="primary" @click="openCreate">新建检测</n-button>
      </n-space>
    </div>

    <!-- 进行中任务的实时进度 -->
    <n-card v-if="activeTaskId" class="progress-card" title="实时进度" size="small">
      <task-progress :task-id="activeTaskId" @done="handleTaskDone" />
    </n-card>

    <n-data-table
      :columns="columns"
      :data="tasks"
      :loading="loading"
      :pagination="{ pageSize: 10 }"
    />

    <!-- 新建任务弹窗 -->
    <n-modal v-model:show="showForm" preset="card" title="新建检测任务" style="width: 560px">
      <task-form ref="formRef" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="showForm = false">取消</n-button>
          <n-button type="primary" :loading="creating" @click="handleCreate">
            发起检测
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NButton, NSpace, NTag, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { tasksApi } from '../api/tasks'
import { useMainStore } from '../stores/main'
import { TASK_STATUS_LABELS, formatDateTime } from '../utils/format'
import TaskForm from '../components/TaskForm.vue'
import TaskProgress from '../components/TaskProgress.vue'

const store = useMainStore()
const message = useMessage()
const router = useRouter()

const tasks = ref([])
const loading = ref(false)
const creating = ref(false)
const showForm = ref(false)
const activeTaskId = ref(null)
const formRef = ref(null)

const STATUS_TYPE = {
  pending: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  canceled: 'warning'
}

const columns = [
  { title: '任务 ID', key: 'id', width: 90 },
  { title: '中转站', key: 'station_id', width: 100 },
  { title: '模型', key: 'model_id', width: 90 },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row) =>
      h(
        NTag,
        { size: 'small', type: STATUS_TYPE[row.status] || 'default', bordered: false },
        { default: () => TASK_STATUS_LABELS[row.status] || row.status }
      )
  },
  {
    title: '进度',
    key: 'progress',
    width: 90,
    render: (row) => `${row.progress ?? 0}%`
  },
  {
    title: '创建时间',
    key: 'created_at',
    render: (row) => formatDateTime(row.created_at)
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) =>
      h(NSpace, { size: 8 }, {
        default: () => [
          row.status === 'completed'
            ? h(
                NButton,
                { size: 'tiny', type: 'primary', tertiary: true, onClick: () => viewReport(row.id) },
                { default: () => '查看报告' }
              )
            : null,
          row.status === 'running' || row.status === 'pending'
            ? h(
                NButton,
                { size: 'tiny', tertiary: true, onClick: () => trackTask(row.id) },
                { default: () => '跟踪进度' }
              )
            : null,
          row.status === 'running' || row.status === 'pending'
            ? h(
                NButton,
                { size: 'tiny', type: 'error', tertiary: true, onClick: () => cancelTask(row.id) },
                { default: () => '取消' }
              )
            : null
        ].filter(Boolean)
      })
  }
]

async function loadTasks() {
  loading.value = true
  try {
    tasks.value = await tasksApi.list()
  } catch (error) {
    message.error(error.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  showForm.value = true
}

async function handleCreate() {
  creating.value = true
  try {
    const payload = formRef.value.collect()
    const task = await tasksApi.create(payload)
    message.success(`任务 #${task.id} 已发起`)
    showForm.value = false
    activeTaskId.value = task.id
    await loadTasks()
  } catch (error) {
    message.error(error.message)
  } finally {
    creating.value = false
  }
}

function trackTask(taskId) {
  activeTaskId.value = taskId
}

function viewReport(taskId) {
  router.push({ name: 'report', params: { taskId: String(taskId) } })
}

async function cancelTask(taskId) {
  try {
    await tasksApi.cancel(taskId)
    message.info(`已请求取消任务 #${taskId}`)
    await loadTasks()
  } catch (error) {
    message.error(error.message)
  }
}

// 进度完成后刷新列表；完成态提示可看报告。
function handleTaskDone({ taskId, phase }) {
  loadTasks()
  if (phase === 'completed') {
    message.success(`任务 #${taskId} 检测完成，可查看报告`)
  }
}

onMounted(loadTasks)
</script>

<style scoped>
.progress-card {
  margin-bottom: 16px;
}
</style>
