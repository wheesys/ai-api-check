// Pinia 全局 store：中转站与模型的缓存与操作封装。
import { defineStore } from 'pinia'
import { stationsApi } from '../api/stations'
import { modelsApi } from '../api/models'

export const useMainStore = defineStore('main', {
  state: () => ({
    stations: [],
    modelsByStation: {}, // { [stationId]: Model[] }
    loading: false
  }),
  actions: {
    async loadStations() {
      this.loading = true
      try {
        this.stations = await stationsApi.list()
      } finally {
        this.loading = false
      }
    },
    async createStation(payload) {
      const station = await stationsApi.create(payload)
      this.stations.push(station)
      return station
    },
    async updateStation(id, payload) {
      const updated = await stationsApi.update(id, payload)
      const index = this.stations.findIndex((s) => s.id === id)
      if (index >= 0) this.stations[index] = updated
      return updated
    },
    async deleteStation(id) {
      await stationsApi.remove(id)
      this.stations = this.stations.filter((s) => s.id !== id)
      delete this.modelsByStation[id]
    },
    async loadModels(stationId) {
      this.modelsByStation[stationId] = await modelsApi.list(stationId)
      return this.modelsByStation[stationId]
    },
    async fetchModels(stationId) {
      const outcome = await modelsApi.fetch(stationId)
      await this.loadModels(stationId)
      return outcome
    },
    async addModel(stationId, payload) {
      const model = await modelsApi.add(stationId, payload)
      await this.loadModels(stationId)
      return model
    }
  }
})
