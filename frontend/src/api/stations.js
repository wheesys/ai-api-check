// 中转站 API。
import client from './client'

export const stationsApi = {
  list: () => client.get('/api/stations').then((r) => r.data),
  get: (id) => client.get(`/api/stations/${id}`).then((r) => r.data),
  create: (payload) => client.post('/api/stations', payload).then((r) => r.data),
  update: (id, payload) => client.put(`/api/stations/${id}`, payload).then((r) => r.data),
  remove: (id) => client.delete(`/api/stations/${id}`)
}
