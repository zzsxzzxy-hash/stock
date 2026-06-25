<template>
  <el-container class="app-container">
    <!-- 侧边栏 -->
    <el-aside :width="collapsed ? '64px' : '220px'" class="aside">
      <div class="logo" @click="$router.push('/')">
        <el-icon size="22"><TrendCharts /></el-icon>
        <span v-show="!collapsed" class="logo-text">InStock</span>
      </div>

      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="activeMenu"
          :collapse="collapsed"
          :collapse-transition="false"
          router
          background-color="#001529"
          text-color="#ffffffa0"
          active-text-color="#409eff"
        >
          <!-- 固定项：首页 -->
          <el-menu-item index="/">
            <el-icon><House /></el-icon>
            <template #title>首页</template>
          </el-menu-item>

          <!-- 固定项：数据同步 -->
          <el-menu-item index="/sync">
            <el-icon><Refresh /></el-icon>
            <template #title>数据同步管理</template>
          </el-menu-item>

          <!-- 固定项：我的关注 -->
          <el-menu-item index="/watchlist">
            <el-icon><Star /></el-icon>
            <template #title>我的关注</template>
          </el-menu-item>

          <!-- 固定项：量能监控（在自有策略同级插入）-->
          <el-sub-menu index="volume_group">
            <template #title>
              <el-icon><DataAnalysis /></el-icon>
              <span>自有策略</span>
            </template>
            <el-menu-item index="/volume_monitor">
              <span>量能异动监控</span>
            </el-menu-item>
            <el-menu-item
              v-for="item in customStrategyItems"
              :key="item.table"
              :index="`/custom/${item.table}`"
            >
              <span>{{ item.name }}</span>
            </el-menu-item>
            <el-menu-item index="/factor_config">
              <span>因子配置</span>
            </el-menu-item>
            <el-menu-item index="/score_single">
              <span>单股票计算</span>
            </el-menu-item>
          </el-sub-menu>

          <!-- 动态菜单分组（自有策略已单独处理，这里排除）-->
          <el-sub-menu
            v-for="group in menuModules.filter(g => g.type !== '自有策略')"
            :key="group.type"
            :index="group.type"
          >
            <template #title>
              <el-icon><component :is="group.icon" /></el-icon>
              <span>{{ group.type }}</span>
            </template>
            <el-menu-item
              v-for="item in group.items"
              :key="item.table"
              :index="item.custom ? `/custom/${item.table}` : `/table/${item.table}`"
            >
              <span>{{ item.name }}</span>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>

      <!-- 折叠按钮 -->
      <div class="collapse-btn" @click="collapsed = !collapsed">
        <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
      </div>
    </el-aside>

    <!-- 主内容 -->
    <el-container direction="vertical" class="main-wrap">
      <el-header class="header">
        <span class="header-title">{{ pageTitle }}</span>
        <div class="header-right">
          <el-text type="info" size="small">InStock 股票分析系统</el-text>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <keep-alive :include="['TableView']">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { menuModules } from '@/config/menus'
import { Star, DataAnalysis } from '@element-plus/icons-vue'

const collapsed = ref(false)
const route = useRoute()

// 自有策略子项（供菜单使用）
const customStrategyItems = computed(() => {
  const g = menuModules.find(m => m.type === '自有策略')
  return g ? g.items : []
})

const activeMenu = computed(() => route.path)

const pageTitle = computed(() => {
  const path = route.path
  if (path === '/') return '首页'
  if (path === '/sync') return '数据同步管理'
  if (path === '/watchlist') return '我的关注'
  if (path === '/indicators') return 'K线指标图表'
  if (path === '/volume_monitor') return '量能异动监控'
  if (path === '/system_health') return '系统运行监控'
  if (path === '/factor_config') return '量能因子配置'
  if (path === '/score_single')  return '单股票计算'
  if (path.startsWith('/table/')) {
    const table = path.replace('/table/', '')
    for (const g of menuModules) {
      const found = g.items.find(i => i.table === table)
      if (found) return found.name
    }
  }
  return 'InStock'
})
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #app { height: 100%; }
</style>

<style scoped>
.app-container { height: 100vh; }

.aside {
  background: #001529;
  transition: width 0.2s;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  cursor: pointer;
  border-bottom: 1px solid #ffffff18;
  flex-shrink: 0;
}
.logo-text { white-space: nowrap; }

.menu-scroll { flex: 1; }

.el-menu { border-right: none; }

.collapse-btn {
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffffa0;
  cursor: pointer;
  border-top: 1px solid #ffffff18;
  flex-shrink: 0;
}
.collapse-btn:hover { color: #fff; background: #ffffff10; }

.main-wrap { min-width: 0; }

.header {
  height: 52px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  flex-shrink: 0;
}
.header-title { font-size: 15px; font-weight: 600; color: #303133; }

.main-content {
  padding: 16px;
  background: #f5f7fa;
  flex: 1;
  overflow: auto;
}
</style>
