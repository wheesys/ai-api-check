// 路由配置：中转站管理、检测任务、报告详情。
import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/stations' },
  {
    path: '/stations',
    name: 'stations',
    component: () => import('../views/StationsView.vue'),
    meta: { title: '中转站管理' }
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('../views/TasksView.vue'),
    meta: { title: '检测任务' }
  },
  {
    path: '/tasks/:taskId/report',
    name: 'report',
    component: () => import('../views/ReportView.vue'),
    meta: { title: '检测报告' },
    props: true
  }
]

export default createRouter({
  history: createWebHashHistory(),
  routes
})
