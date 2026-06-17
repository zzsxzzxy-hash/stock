import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/',                    component: () => import('@/views/HomeView.vue') },
  { path: '/table/:table',        component: () => import('@/views/TableView.vue') },
  { path: '/custom/:table',       component: () => import('@/views/CustomStrategyView.vue') },
  { path: '/indicators',          component: () => import('@/views/IndicatorsView.vue') },
  { path: '/sync',                component: () => import('@/views/SyncView.vue') },
  { path: '/watchlist',           component: () => import('@/views/WatchlistView.vue') },
]

export default createRouter({
  history: createWebHistory('/app/'),
  routes,
})
