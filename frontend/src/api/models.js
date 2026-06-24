// 模型 API（归属某中转站）。
import client from './client'

export const modelsApi = {
  list: (stationId) =>
    client.get(`/api/stations/${stationId}/models`).then((r) => r.data),
  add: (stationId, payload) =>
    client.post(`/api/stations/${stationId}/models`, payload).then((r) => r.data),
  fetch: (stationId) =>
    client.post(`/api/stations/${stationId}/models/fetch`).then((r) => r.data)
}
