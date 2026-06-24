// 应用入口：装配 Vue + Pinia + Router + Naive UI。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import App from './App.vue'
import router from './router'
import './assets/styles.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(naive)
app.mount('#app')
