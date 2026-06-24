// 报告 API：结果汇总、策略明细、PDF 导出。
import client from './client'

export const reportsApi = {
  result: (taskId) => client.get(`/api/tasks/${taskId}/result`).then((r) => r.data),
  strategies: (taskId) =>
    client.get(`/api/tasks/${taskId}/strategies`).then((r) => r.data),
  probes: (taskId, strategyId) =>
    client.get(`/api/tasks/${taskId}/strategies/${strategyId}/probes`).then((r) => r.data),
  // PDF 导出：携带前端图表 base64，返回二进制 blob
  exportPdf: (taskId, charts) =>
    client
      .post(`/api/tasks/${taskId}/report.pdf`, { charts }, { responseType: 'blob' })
      .then((r) => r.data)
}
