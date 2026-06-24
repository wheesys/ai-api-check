<!-- 中转站表单：创建与编辑共用。协议集合多选 + 自定义地址/名称/Key。 -->
<!-- 编辑态下 api_key 留空表示不修改（与后端 RelayStationUpdate 语义对齐）。 -->
<template>
  <n-form ref="formRef" :model="form" :rules="rules" label-placement="top">
    <n-form-item label="中转站名称" path="name">
      <n-input v-model:value="form.name" placeholder="例如：某中转 Pro" />
    </n-form-item>

    <n-form-item label="协议集合（可多选）" path="protocols">
      <n-checkbox-group v-model:value="form.protocols">
        <n-space>
          <n-checkbox value="openai" label="OpenAI" />
          <n-checkbox value="anthropic" label="Anthropic" />
          <n-checkbox value="gemini" label="Gemini" />
        </n-space>
      </n-checkbox-group>
    </n-form-item>
    <p class="muted form-hint">
      兼容站可同时勾选 OpenAI / Anthropic；单协议站仅勾选其一。
    </p>

    <n-form-item label="API 基础地址" path="base_url">
      <n-input v-model:value="form.base_url" placeholder="https://api.example.com" />
    </n-form-item>

    <n-form-item :label="apiKeyLabel" path="api_key">
      <n-input
        v-model:value="form.api_key"
        type="password"
        show-password-on="click"
        :placeholder="isEdit ? '留空则不修改' : '请输入 API Key'"
      />
    </n-form-item>

    <n-form-item label="状态" path="status">
      <n-select v-model:value="form.status" :options="statusOptions" />
    </n-form-item>
  </n-form>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  // 编辑态传入既有站点（含 has_api_key）；创建态为 null。
  station: { type: Object, default: null }
})

const formRef = ref(null)
const isEdit = computed(() => props.station !== null)
const apiKeyLabel = computed(() =>
  isEdit.value ? 'API Key（留空不改）' : 'API Key'
)

const statusOptions = [
  { label: '启用', value: 'active' },
  { label: '禁用', value: 'disabled' }
]

const form = reactive({
  name: '',
  protocols: [],
  base_url: '',
  api_key: '',
  status: 'active'
})

// 编辑态回填（api_key 不回显，始终留空）。
watch(
  () => props.station,
  (station) => {
    if (station) {
      form.name = station.name
      form.protocols = [...station.protocols]
      form.base_url = station.base_url
      form.status = station.status
      form.api_key = ''
    }
  },
  { immediate: true }
)

const rules = {
  name: { required: true, message: '请输入中转站名称', trigger: 'blur' },
  protocols: {
    type: 'array',
    required: true,
    min: 1,
    message: '至少选择一个协议',
    trigger: 'change'
  },
  base_url: { required: true, message: '请输入 API 基础地址', trigger: 'blur' },
  api_key: {
    trigger: 'blur',
    validator: (_rule, value) => {
      // 创建态必填；编辑态可空（表示不改）。
      if (!isEdit.value && !value) return new Error('请输入 API Key')
      return true
    }
  }
}

// 校验并产出提交载荷：编辑态省略空 api_key。
async function validateAndCollect() {
  await formRef.value?.validate()
  const payload = {
    name: form.name,
    protocols: form.protocols,
    base_url: form.base_url,
    status: form.status
  }
  if (form.api_key) payload.api_key = form.api_key
  return payload
}

defineExpose({ validateAndCollect })
</script>

<style scoped>
.form-hint {
  margin: -8px 0 12px;
}
</style>
