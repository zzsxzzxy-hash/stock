import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/',                    component: () => import('@/views/HomeView.vue') },
  { path: '/table/:table',        component: () => import('@/views/TableView.vue') },
  { path: '/custom/:table',       component: () => import('@/views/CustomStrategyView.vue') },
  { path: '/volume_monitor',      component: () => import('@/views/VolumeMonitorView.vue') },
  { path: '/leader_strength',     component: () => import('@/views/LeaderStrengthView.vue') },
  { path: '/indicators',          component: () => import('@/views/IndicatorsView.vue') },
  { path: '/sync',                component: () => import('@/views/SyncView.vue') },
  { path: '/watchlist',           component: () => import('@/views/WatchlistView.vue') },
  { path: '/operation_strategy',  component: () => import('@/views/OperationStrategyView.vue') },
  { path: '/operation_journal',   component: () => import('@/views/OperationJournalView.vue') },
  { path: '/system_health',       component: () => import('@/views/SystemHealthView.vue') },
  { path: '/factor_config',       component: () => import('@/views/FactorConfigView.vue') },
  { path: '/score_single',         component: () => import('@/views/ScoreSingleView.vue') },
]

export default createRouter({
  history: createWebHistory('/app/'),
  routes,
})
