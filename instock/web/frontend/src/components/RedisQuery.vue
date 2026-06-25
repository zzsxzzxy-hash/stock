<template>
  <div class="redis-query">
    <!-- 搜索栏 -->
    <el-card shadow="never" class="search-card">
      <el-row :gutter="12" align="middle">
        <el-col :span="6">
          <el-select v-model="queryDate" placeholder="选择日期" clearable style="width:100%"
                     @change="onDateChange">
            <el-option v-for="d in dates" :key="d" :label="d" :value="d" />
          </el-select>
        </el-col>
        <el-col :span="5">
          <el-input v-model="queryCode" placeholder="股票代码（可选）" clearable
                    maxlength="6" @keyup.enter="doQuery" />
        </el-col>
        <el-col :span="3">
          <el-button type="primary" :loading="loading" @click="doQuery" style="width:100%">查询</el-button>
        </el-col>
        <el-col :span="10">
          <el-text type="info" size="small" v-if="overview">
            {{ queryDate }} 共 {{ overview.total_codes }} 只股票有分钟数据
          </el-text>
        </el-col>
      </el-row>
    </el-card>

    <!-- 概览（未输入股票代码时） -->
    <el-card shadow="never" class="result-card" v-if="overview && !queryCode">
      <template #header>
        <span>日期概览：{{ queryDate }}</span>
      </template>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="日期">{{ overview.date }}</el-descriptions-item>
        <el-descriptions-item label="股票数量">
          <el-tag type="success">{{ overview.total_codes }} 只</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <div class="sample-title">数据样本（前5只）</div>
      <el-table :data="overview.samples" size="small" border style="margin-top:8px">
        <el-table-column prop="code"       label="代码"   width="90" />
        <el-table-column prop="bar_count"  label="分钟数" width="80" />
        <el-table-column prop="time_range" label="时间范围" />
      </el-table>
    </el-card>

    <!-- 单股详情 -->
    <template v-if="stockData && queryCode">
      <!-- 统计信息 -->
      <el-card shadow="never" class="result-card">
        <template #header>
          <div style="display:flex;align-items:center;gap:8px">
            <span>{{ stockData.name || stockData.code }}（{{ stockData.code }}）</span>
            <el-tag type="success" size="small" v-if="stockData.bars?.length">
              {{ stockData.stats?.bar_count }} 根K线
            </el-tag>
            <el-tag type="warning" size="small" v-else>Redis 无数据</el-tag>
          </div>
        </template>

        <template v-if="stockData.bars?.length">
          <el-descriptions :column="4" border size="small">
            <el-descriptions-item label="时间范围">{{ stockData.stats.time_range }}</el-descriptions-item>
            <el-descriptions-item label="K线数">{{ stockData.stats.bar_count }}</el-descriptions-item>
            <el-descriptions-item label="开盘价">{{ stockData.stats.price_open }}</el-descriptions-item>
            <el-descriptions-item label="最新价">{{ stockData.stats.price_last }}</el-descriptions-item>
            <el-descriptions-item label="最高价">{{ stockData.stats.price_high }}</el-descriptions-item>
            <el-descriptions-item label="最低价">{{ stockData.stats.price_low }}</el-descriptions-item>
            <el-descriptions-item label="总成交量">{{ stockData.stats.total_vol?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="均量/分钟">{{ stockData.stats.avg_vol?.toLocaleString() }}</el-descriptions-item>
          </el-descriptions>
        </template>
        <el-empty v-else description="该股票在 Redis 中无数据" :image-size="60" />
      </el-card>

      <!-- 分钟K线明细表 -->
      <el-card shadow="never" class="result-card" v-if="stockData.bars?.length">
        <template #header>
          <div style="display:flex;align-items:center;justify-content:space-between">
            <span>分钟K线明细（{{ stockData.bars.length }} 根）</span>
            <el-input v-model="barFilter" placeholder="过滤时间..." size="small"
                      clearable style="width:140px" />
          </div>
        </template>
        <el-table :data="filteredBars" size="small" border
                  height="460" :highlight-current-row="true">
          <el-table-column prop="time"      label="时间"   width="70" fixed />
          <el-table-column prop="open"      label="开"     width="80" align="right" />
          <el-table-column prop="close"     label="收"     width="80" align="right">
            <template #default="{ row }">
              <span :style="{ color: row.close >= row.open ? '#f56c6c' : '#67c23a' }">
                {{ row.close }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="high"      label="高"     width="80" align="right" />
          <el-table-column prop="low"       label="低"     width="80" align="right" />
          <el-table-column prop="volume"    label="成交量(手)" width="110" align="right">
            <template #default="{ row }">{{ row.volume?.toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="amount"    label="成交额(元)" align="right">
            <template #default="{ row }">{{ row.amount?.toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="pre_close" label="前收"   width="80" align="right" />
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const queryDate  = ref('')
const queryCode  = ref('')
const loading    = ref(false)
const dates      = ref([])
const overview   = ref(null)
const stockData  = ref(null)
const barFilter  = ref('')

const filteredBars = computed(() => {
  if (!stockData.value?.bars) return []
  if (!barFilter.value) return stockData.value.bars
  return stockData.value.bars.filter(b => b.time.includes(barFilter.value))
})

async function loadDates() {
  try {
    const res = await axios.get('/api/redis_dates')
    dates.value = res.data.dates || []
    if (dates.value.length) queryDate.value = dates.value[0]
  } catch (e) {
    ElMessage.error('获取日期列表失败')
  }
}

function onDateChange() {
  overview.value  = null
  stockData.value = null
  if (queryDate.value && !queryCode.value) doQuery()
}

async function doQuery() {
  if (!queryDate.value) {
    ElMessage.warning('请先选择日期')
    return
  }
  loading.value = true
  overview.value  = null
  stockData.value = null
  try {
    const params = { date: queryDate.value }
    if (queryCode.value) params.code = queryCode.value
    const res = await axios.get('/api/redis_query', { params })
    if (queryCode.value) {
      stockData.value = res.data
    } else {
      overview.value = res.data
    }
  } catch (e) {
    ElMessage.error('查询失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadDates()
  if (queryDate.value) doQuery()
})
</script>

<style scoped>
.redis-query { display: flex; flex-direction: column; gap: 12px; }
.search-card :deep(.el-card__body) { padding: 12px 16px; }
.result-card :deep(.el-card__body) { padding: 12px; }
.sample-title { font-size: 12px; color: #606266; margin-top: 12px; font-weight: 600; }
</style>
