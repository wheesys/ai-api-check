// 格式化与本地化工具。

// 真实性分级中文名与色调（与后端 AuthenticityLevel 对齐）。
export const AUTH_LEVEL_LABELS = {
  normal: { text: '正常', type: 'success' },
  suspicious: { text: '可能可疑', type: 'warning' },
  highly_suspicious: { text: '高度可疑', type: 'error' }
}

// 策略三态中文名与色调。
export const STATUS_LABELS = {
  pass: { text: '通过', type: 'success' },
  degraded: { text: '降级', type: 'warning' },
  fail: { text: '失败', type: 'error' },
  skipped: { text: '跳过', type: 'default' }
}

// 任务状态中文名。
export const TASK_STATUS_LABELS = {
  pending: '排队中',
  running: '检测中',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消'
}

// 协议中文名。
export const PROTOCOL_LABELS = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini'
}

// 评分格式化：null/undefined → 占位符。
export function formatScore(value, placeholder = '—') {
  if (value === null || value === undefined) return placeholder
  return Number(value).toFixed(1)
}

// ISO 时间 → 本地可读字符串。
export function formatDateTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}

// 触发浏览器下载 Blob。
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
