// Axios 客户端：统一基地址与错误归一。
// 后端错误响应形如 { error, message }（已脱敏），拦截器提取 message 便于 UI 提示。
import axios from 'axios'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' }
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    const data = error.response?.data
    const message = data?.message || data?.detail || error.message || '请求失败'
    return Promise.reject(new Error(message))
  }
)

export default client
