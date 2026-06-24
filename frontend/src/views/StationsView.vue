<!-- 中转站管理视图：站点列表 + 新建/编辑 + 展开管理模型。 -->
<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">中转站管理</h1>
        <p class="muted">配置中转站（协议集合 / 地址 / Key），并管理其可检测模型。</p>
      </div>
      <n-button type="primary" @click="openCreate">新建中转站</n-button>
    </div>

    <n-spin :show="store.loading">
      <n-empty v-if="!store.stations.length" description="暂无中转站，点击右上角新建" />
      <n-collapse v-else accordion :default-expanded-names="defaultExpanded">
        <n-collapse-item
          v-for="station in store.stations"
          :key="station.id"
          :name="String(station.id)"
        >
          <template #header>
            <div class="station-head">
              <span class="station-name">{{ station.name }}</span>
              <n-tag
                v-for="p in station.protocols"
                :key="p"
                size="small"
                :bordered="false"
                style="margin-left: 6px"
              >
                {{ protocolLabel(p) }}
              </n-tag>
              <n-tag
                size="small"
                :type="station.status === 'active' ? 'success' : 'default'"
                :bordered="false"
                style="margin-left: 6px"
              >
                {{ station.status === 'active' ? '启用' : '禁用' }}
              </n-tag>
            </div>
          </template>
          <template #header-extra>
            <n-space :size="8" @click.stop>
              <n-button size="tiny" tertiary @click="openEdit(station)">编辑</n-button>
              <n-button size="tiny" tertiary type="error" @click="confirmDelete(station)">
                删除
              </n-button>
            </n-space>
          </template>

          <p class="muted station-url">{{ station.base_url }}</p>
          <model-panel :station-id="station.id" />
        </n-collapse-item>
      </n-collapse>
    </n-spin>

    <!-- 新建/编辑弹窗 -->
    <n-modal
      v-model:show="showForm"
      preset="card"
      :title="editing ? '编辑中转站' : '新建中转站'"
      style="width: 560px"
    >
      <station-form ref="formRef" :station="editing" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="showForm = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="handleSave">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import { useMainStore } from '../stores/main'
import { PROTOCOL_LABELS } from '../utils/format'
import StationForm from '../components/StationForm.vue'
import ModelPanel from '../components/ModelPanel.vue'

const store = useMainStore()
const message = useMessage()
const dialog = useDialog()

const showForm = ref(false)
const editing = ref(null)
const saving = ref(false)
const formRef = ref(null)
const defaultExpanded = ref([])

function protocolLabel(protocol) {
  return PROTOCOL_LABELS[protocol] || protocol
}

function openCreate() {
  editing.value = null
  showForm.value = true
}

function openEdit(station) {
  editing.value = station
  showForm.value = true
}

async function handleSave() {
  saving.value = true
  try {
    const payload = await formRef.value.validateAndCollect()
    if (editing.value) {
      await store.updateStation(editing.value.id, payload)
      message.success('已更新')
    } else {
      await store.createStation(payload)
      message.success('已创建')
    }
    showForm.value = false
  } catch (error) {
    // 校验错误为数组，提交错误为 Error；统一给出提示。
    if (error instanceof Error) message.error(error.message)
  } finally {
    saving.value = false
  }
}

function confirmDelete(station) {
  dialog.warning({
    title: '删除确认',
    content: `确定删除中转站「${station.name}」及其全部模型？此操作不可撤销。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.deleteStation(station.id)
        message.success('已删除')
      } catch (error) {
        message.error(error.message)
      }
    }
  })
}

onMounted(async () => {
  try {
    await store.loadStations()
  } catch (error) {
    message.error(error.message)
  }
})
</script>

<style scoped>
.station-head {
  display: flex;
  align-items: center;
}
.station-name {
  font-weight: 600;
}
.station-url {
  margin: 4px 0 12px;
  font-family: 'SFMono-Regular', Menlo, monospace;
}
</style>
