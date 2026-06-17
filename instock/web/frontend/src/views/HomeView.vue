<template>
  <div class="home">
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="s in stats" :key="s.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-inner">
            <el-icon :size="32" :color="s.color"><component :is="s.icon" /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ s.value }}</div>
              <div class="stat-label">{{ s.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="14">
        <el-card shadow="never" header="快速入口">
          <el-space wrap>
            <el-button
              v-for="item in quickLinks"
              :key="item.table"
              :icon="item.icon"
              type="primary"
              plain
              @click="$router.push(`/table/${item.table}`)"
            >{{ item.name }}</el-button>
          </el-space>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never" header="系统信息">
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="当前交易日">{{ tradeDate }}</el-descriptions-item>
            <el-descriptions-item label="系统时间">{{ systemTime }}</el-descriptions-item>
            <el-descriptions-item label="前端框架">Vue 3 + Vite + Element Plus</el-descriptions-item>
            <el-descriptions-item label="后端框架">Python Tornado</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { fetchTradeDate } from '@/api'

const tradeDate = ref('加载中...')
const systemTime = ref('')

const stats = ref([
  { label: '每日股票数据',   value: '-', icon: 'DataBoard',   color: '#409eff' },
  { label: '综合选股',       value: '-', icon: 'Monitor',      color: '#67c23a' },
  { label: '股票指标数据',   value: '-', icon: 'TrendCharts',  color: '#e6a23c' },
  { label: '关注股票',       value: '-', icon: 'Star',         color: '#f56c6c' },
])

const quickLinks = [
  { name: '每日股票数据',  table: 'cn_stock_spot',             icon: 'DataBoard'   },
  { name: '综合选股',      table: 'cn_stock_selection',        icon: 'Monitor'     },
  { name: '股票指标买入',  table: 'cn_stock_indicators_buy',   icon: 'TrendCharts' },
  { name: '龙虎榜',        table: 'cn_stock_lhb',              icon: 'Trophy'      },
  { name: '资金流向',      table: 'cn_stock_fund_flow',        icon: 'Money'       },
  { name: '大宗交易',      table: 'cn_stock_blocktrade',       icon: 'Goods'       },
]

let timer = null

function updateTime() {
  systemTime.value = new Date().toLocaleString('zh-CN')
}

onMounted(async () => {
  updateTime()
  timer = setInterval(updateTime, 1000)
  try {
    const res = await fetchTradeDate()
    tradeDate.value = res.data.date
  } catch {
    tradeDate.value = new Date().toISOString().slice(0, 10)
  }
})

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.home { padding: 0; }
.stat-row { margin-bottom: 0; }
.stat-card :deep(.el-card__body) { padding: 16px; }
.stat-inner {
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-info { flex: 1; }
.stat-value { font-size: 24px; font-weight: 700; color: #303133; }
.stat-label { font-size: 13px; color: #909399; margin-top: 4px; }
</style>
