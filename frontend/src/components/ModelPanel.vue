<!-- 模型面板：展示某中转站模型列表，支持自动拉取与手动录入（拉取失败回退）。 -->
<template>
  <div class="model-panel">
    <div class="panel-actions">
      <n-button size="small" :loading="fetching" @click="handleFetch">
        自动拉取模型
      </n-button>
      <n-button size="small" tertiary @click="showManual = true">
        手动录入
      </n-button>
      <n-button size="small" quaternary :loading="loading" @click="reload">
        刷新
      </n-button>
    </div>

    <n-data-table
      size="small"
      :columns="columns"
      :data="models"
      :loading="loading"
      :bordered="false"
      :pagination="{ pageSize: 8 }"
    />

    <!-- 拉取结果反馈：逐协议成功/失败与是否回退手输。 -->
    <n-alert
      v-if="fetchOutcome"
      class="fetch-outcome"
      :type="fetchOutcome.fallback_manual ? 'warning' : 'success'"
      :title="fetchOutcomeTitle"
    >
      <template v-if="fetchOutcome.failures.length">
        失败：
        <span v-for="(f, i) in fetchOutcome.failures" :key="i" class="fail-item">
          {{ protocolLabel(f.protocol) }}/{{ f.access_mode }} — {{ f.reason }}
        </span>
      </template>
    </n-alert>

    <!-- 手动录入弹窗 -->
    <n-modal
      v-model:show="showManual"
      preset="card"
      title="手动录入模型"
      style="width: 520px"
    >
      <n-form :model="manual" label-placement="top">
        <n-form-item label="协议">
          <n-select v-model:value="manual.protocol" :options="protocolOptions" />
        </n-form-item>
        <n-form-item label="接入形态">
          <n-select v-model:value="manual.access_mode" :options="accessModeOptions" />
        </n-form-item>
        <n-form-item label="模型标识名">
          <n-input v-model:value="manual.model_name" placeholder="例如：gpt-4o" />
        </n-form-item>
        <n-form-item label="展示名（可选）">
          <n-input v-model:value="manual.display_name" />
        </n-form-item>
        <n-grid :cols="2" :x-gap="12">
          <n-gi>
            <n-form-item label="输入单价（可选）">
              <n-input-number
                v-model:value="manual.input_price"
                :min="0"
                :precision="6"
                placeholder="每百万 token"
                style="width: 100%"
              />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="输出单价（可选）">
              <n-input-number
                v-model:value="manual.output_price"
                :min="0"
                :precision="6"
                style="width: 100%"
              />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-form-item label="声明上下文长度（可选）">
          <n-input-number
            v-model:value="manual.declared_context_length"
            :min="0"
            style="width: 100%"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showManual = false">取消</n-button>
          <n-button type="primary" :loading="adding" @click="handleAddManual">
            录入
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NTag } from 'naive-ui'
import { useMessage } from 'naive-ui'
import { useMainStore } from '../stores/main'
import { PROTOCOL_LABELS } from '../utils/format'

const props = defineProps({
  stationId: { type: Number, required: true }
})

const store = useMainStore()
const message = useMessage()

const loading = ref(false)
const fetching = ref(false)
const adding = ref(false)
const showManual = ref(false)
const fetchOutcome = ref(null)

const models = computed(() => store.modelsByStation[props.stationId] || [])

const protocolOptions = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'Anthropic', value: 'anthropic' },
  { label: 'Gemini', value: 'gemini' }
]
const accessModeOptions = [
  { label: '原生（native）', value: 'native' },
  { label: 'OpenAI 兼容层', value: 'openai_compat' }
]

const manual = reactive({
  station_id: props.stationId,
  protocol: 'openai',
  access_mode: 'native',
  model_name: '',
  display_name: '',
  input_price: null,
  output_price: null,
  declared_context_length: null,
  source: 'manual'
})

const columns = [
  {
    title: '协议',
    key: 'protocol',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false }, { default: () => protocolLabel(row.protocol) })
  },
  { title: '接入', key: 'access_mode', width: 130 },
  { title: '模型', key: 'model_name', ellipsis: { tooltip: true } },
  {
    title: '来源',
    key: 'source',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { size: 'small', type: row.source === 'fetched' ? 'info' : 'default', bordered: false },
        { default: () => (row.source === 'fetched' ? '拉取' : '手输') }
      )
  }
]

const fetchOutcomeTitle = computed(() => {
  if (!fetchOutcome.value) return ''
  const got = fetchOutcome.value.fetched.length
  return fetchOutcome.value.fallback_manual
    ? `自动拉取未获得模型，请手动录入（成功 ${got} 个）`
    : `拉取完成，新增/更新 ${got} 个模型`
})

function protocolLabel(protocol) {
  return PROTOCOL_LABELS[protocol] || protocol
}

async function reload() {
  loading.value = true
  try {
    await store.loadModels(props.stationId)
  } catch (error) {
    message.error(error.message)
  } finally {
    loading.value = false
  }
}

async function handleFetch() {
  fetching.value = true
  fetchOutcome.value = null
  try {
    fetchOutcome.value = await store.fetchModels(props.stationId)
  } catch (error) {
    message.error(error.message)
  } finally {
    fetching.value = false
  }
}

async function handleAddManual() {
  if (!manual.model_name) {
    message.warning('请填写模型标识名')
    return
  }
  adding.value = true
  try {
    await store.addModel(props.stationId, { ...manual })
    message.success('已录入')
    showManual.value = false
    manual.model_name = ''
    manual.display_name = ''
  } catch (error) {
    message.error(error.message)
  } finally {
    adding.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.panel-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.fetch-outcome {
  margin-top: 12px;
}
.fail-item {
  display: inline-block;
  margin-right: 12px;
}
</style>
