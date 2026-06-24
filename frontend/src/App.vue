<!-- 应用根组件：全局 Provider 包裹 + 顶部导航 + 路由出口。 -->
<!-- 全局注入 message/dialog Provider，使各视图可经 useMessage/useDialog 反馈。 -->
<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <div class="app-shell">
          <header class="app-header">
            <div class="app-brand">
              <span class="brand-mark">◆</span>
              <span class="brand-text">中转站模型质量检测平台</span>
            </div>
            <n-menu
              mode="horizontal"
              :options="menuOptions"
              :value="activeKey"
              @update:value="handleNavigate"
            />
          </header>
          <main>
            <router-view />
          </main>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// 主题微调：主色与平台风格一致。
const themeOverrides = {
  common: { primaryColor: '#2f6fed', primaryColorHover: '#4b85f5' }
}

// 顶部导航项（与路由 name 对齐）。
const menuOptions = [
  { label: () => h('span', '中转站管理'), key: 'stations' },
  { label: () => h('span', '检测任务'), key: 'tasks' }
]

// 报告页归属任务模块，高亮“检测任务”。
const activeKey = computed(() =>
  route.name === 'report' ? 'tasks' : route.name
)

function handleNavigate(key) {
  router.push({ name: key })
}
</script>

<style scoped>
.app-shell {
  min-height: 100%;
}
.app-header {
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 0 24px;
  height: 56px;
  background: #fff;
  border-bottom: 1px solid #e3e8ef;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.app-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 16px;
  white-space: nowrap;
}
.brand-mark {
  color: #2f6fed;
}
</style>
