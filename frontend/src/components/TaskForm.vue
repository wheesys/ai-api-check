<!-- 检测任务表单：选择中转站与模型，可选检测配置（预算 / 声明能力）。 -->
<template>
  <n-form :model="form" label-placement="top">
    <n-form-item label="中转站">
      <n-select
        v-model:value="form.station_id"
        :options="stationOptions"
        placeholder="选择中转站"
        @update:value="handleStationChange"
      />
    </n-form-item>

    <n-form-item label="模型">
      <n-select
        v-model:value="form.model_id"
        :options="modelOptions"
        :loading="modelLoading"
        :disabled="!form.station_id"
        placeholder="选择待检测模型"
      />
    </n-form-item>

    <n-collapse>
      <n-collapse-item title="高级配置（可选）" name="advanced">
        <n-form-item label="检测预算（最大请求数）">
          <n-input-number
            v-model:value="form.max_requests"
            :min="1"
            placeholder="留空使用默认预算"
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="声明能力（启用对应能力探针）">
          <n-checkbox-group v-model:value="form.declared_capabilities">
            <n-space>
              <n-checkbox value="multimodal" label="多模态输入" />
            </n-space>
          </n-checkbox-group>
        </n-form-item>
        <p class="muted">
          流式 / 函数调用 / JSON / 上下文等能力探针默认全检；多模态仅对声明的模型探测，避免误判负分。
        </p>
      </n-collapse-item>
    </n-collapse>
  </n-form>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useMainStore } from '../stores/main'
import { PROTOCOL_LABELS } from '../utils/format'

const store = useMainStore()
const message = useMessage()
const modelLoading = ref(false)

const form = reactive({
  station_id: null,
  model_id: null,
  max_requests: null,
  declared_capabilities: []
})

const stationOptions = computed(() =>
  store.stations.map((s) => ({ label: s.name, value: s.id }))
)

const modelOptions = computed(() => {
  const models = store.modelsByStation[form.station_id] || []
  return models.map((m) => ({
    label: `${PROTOCOL_LABELS[m.protocol] || m.protocol} · ${m.model_name}（${m.access_mode}）`,
    value: m.id
  }))
})

async function handleStationChange(stationId) {
  form.model_id = null
  if (!stationId) return
  modelLoading.value = true
  try {
    await store.loadModels(stationId)
  } catch (error) {
    message.error(error.message)
  } finally {
    modelLoading.value = false
  }
}

// 产出创建任务载荷：config 仅在有自定义项时携带，否则为 null（用后端默认）。
function collect() {
  if (!form.station_id || !form.model_id) {
    throw new Error('请选择中转站与模型')
  }
  const config = {}
  if (form.max_requests) config.max_requests = form.max_requests
  if (form.declared_capabilities.length) {
    config.declared_capabilities = [...form.declared_capabilities]
  }
  return {
    station_id: form.station_id,
    model_id: form.model_id,
    config: Object.keys(config).length ? config : null
  }
}

defineExpose({ collect })
</script>

<style scoped>
.muted {
  margin-top: 4px;
}
</style>
