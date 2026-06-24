<!-- 通用 ECharts 封装：传入 option 即渲染，暴露 getDataURL 供报告 PDF 截图内联。 -->
<!-- 单一封装避免每种图表各写一个组件（DRY）；图表语义差异全部下沉到 chart_config.js。 -->
<template>
  <div ref="el" class="chart-box"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, default: null }
})

const el = ref(null)
let chart = null

function render() {
  if (!chart || !props.option) return
  chart.setOption(props.option, true)
}

function handleResize() {
  chart?.resize()
}

// 导出当前图表为 PNG data-uri（白底，供 PDF 内联）。
function getDataURL() {
  if (!chart) return ''
  return chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', handleResize)
})

watch(() => props.option, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

defineExpose({ getDataURL })
</script>
