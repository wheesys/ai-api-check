// ECharts 配置构建器：从后端结果数据装配各图表 option（与组件解耦，便于复用与测试）。

// 平台统一色板。
const COLORS = {
  primary: '#2f6fed',
  shell: '#f0a020',
  direct: '#2f6fed',
  pass: '#18a058',
  fail: '#d03050',
  axis: '#8a96a3'
}

// 五维雷达：综合质量画像。null 维度按 0 兜底并在标签注明不可用。
export function buildOverallRadar(result) {
  const dims = [
    { key: 'connectivity_score', name: '连通性' },
    { key: 'performance_score', name: '性能' },
    { key: 'billing_score', name: '计费一致性' },
    { key: 'capability_score', name: '能力' },
    { key: 'authenticity_score', name: '真实性' }
  ]
  const indicator = dims.map((d) => ({ name: d.name, max: 100 }))
  const value = dims.map((d) => {
    const score = result?.[d.key]
    return score === null || score === undefined ? 0 : Number(score.toFixed(1))
  })
  return {
    tooltip: {},
    radar: {
      indicator,
      radius: '65%',
      splitLine: { lineStyle: { color: '#e3e8ef' } },
      axisName: { color: '#1f2933' }
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value,
            name: '维度得分',
            areaStyle: { color: 'rgba(47, 111, 237, 0.18)' },
            lineStyle: { color: COLORS.primary }
          }
        ]
      }
    ]
  }
}

// 真实性双子分柱状：套壳子分 vs 直供子分 + 综合（取短板）。
export function buildAuthenticityBar(subscores) {
  if (!subscores) return null
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 70, right: 24, top: 24, bottom: 32 },
    xAxis: { type: 'value', max: 100, axisLine: { lineStyle: { color: COLORS.axis } } },
    yAxis: {
      type: 'category',
      data: ['综合真实性', '直供子分', '套壳子分'],
      axisLine: { lineStyle: { color: COLORS.axis } }
    },
    series: [
      {
        type: 'bar',
        barWidth: 22,
        data: [
          { value: numeric(subscores.authenticity_score), itemStyle: { color: COLORS.primary } },
          { value: numeric(subscores.direct_score), itemStyle: { color: COLORS.direct } },
          { value: numeric(subscores.shell_score), itemStyle: { color: COLORS.shell } }
        ],
        label: { show: true, position: 'right', formatter: ({ value }) => value.toFixed(1) }
      }
    ]
  }
}

// 性能趋势：从性能类策略 metrics 提取 TTFT / 吞吐（无数据则返回 null）。
export function buildPerformanceChart(performanceStrategies) {
  const samples = performanceStrategies
    .map((s) => parseMetrics(s.metrics_json))
    .filter(Boolean)
  if (!samples.length) return null
  const labels = performanceStrategies.map((s) => s.strategy_name)
  const ttft = samples.map((m) => numericOrNull(m.ttft_ms ?? m.ttft))
  const throughput = samples.map((m) => numericOrNull(m.throughput ?? m.tokens_per_second))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['TTFT(ms)', '吞吐(tok/s)'] },
    grid: { left: 50, right: 50, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: labels },
    yAxis: [
      { type: 'value', name: 'TTFT(ms)' },
      { type: 'value', name: 'tok/s' }
    ],
    series: [
      { name: 'TTFT(ms)', type: 'bar', data: ttft, itemStyle: { color: COLORS.shell } },
      {
        name: '吞吐(tok/s)',
        type: 'line',
        yAxisIndex: 1,
        data: throughput,
        itemStyle: { color: COLORS.primary }
      }
    ]
  }
}

function numeric(value) {
  return value === null || value === undefined ? 0 : Number(Number(value).toFixed(1))
}
function numericOrNull(value) {
  return value === null || value === undefined ? null : Number(value)
}
function parseMetrics(metricsJson) {
  if (!metricsJson) return null
  try {
    return JSON.parse(metricsJson)
  } catch {
    return null
  }
}
