import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置：开发代理后端 /api 与 /health，避免本地跨域。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 大型第三方库单独分包，改善首屏加载并消除体积告警。
        manualChunks: {
          echarts: ['echarts'],
          'naive-ui': ['naive-ui']
        }
      }
    }
  }
})
