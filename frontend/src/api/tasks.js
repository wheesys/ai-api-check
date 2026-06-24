// 检测任务 API。
import client from './client'

export const tasksApi = {
  list: () => client.get('/api/tasks').then((r) => r.data),
  get: (id) => client.get(`/api/tasks/${id}`).then((r) => r.data),
  create: (payload) => client.post('/api/tasks', payload).then((r) => r.data),
  cancel: (id) => client.post(`/api/tasks/${id}/cancel`).then((r) => r.data),
  // SSE 进度流 URL（由 sse_client 用 EventSource 订阅）
  eventsUrl: (id) => `${import.meta.env.VITE_API_BASE || ''}/api/tasks/${id}/events`
}
