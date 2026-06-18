<template>
  <div class="cs-page">
    <!-- 工具栏 -->
    <el-card class="toolbar" shadow="never">
      <el-row :gutter="12" align="middle">
        <el-col :span="5">
          <el-date-picker
            v-model="currentDate"
            type="date"
            placeholder="选择信号日期"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
            :disabled-date="d => d > new Date()"
            @change="onDateChange"
            style="width:100%"
          />
        </el-col>
        <el-col :span="5">
          <el-input
            v-model="searchText"
            placeholder="搜索代码/名称"
            clearable
            :prefix-icon="Search"
            @input="onSearch"
          />
        </el-col>
        <el-col :span="14" style="text-align:right; display:flex; align-items:center; justify-content:flex-end; gap:8px;">
          <el-text type="info" size="small">共 {{ total }} 条</el-text>
          <el-button :icon="RefreshRight" @click="loadPage(1)" :loading="loading">刷新</el-button>
          <!-- 实时更新按钮 -->
          <el-button
            :type="realtimeActive ? 'danger' : 'success'"
            :icon="realtimeActive ? VideoPause : VideoPlay"
            @click="toggleRealtime"
          >{{ realtimeActive ? '关闭实时更新' : '更新实时数据' }}</el-button>
          <el-button :icon="Download" @click="exportExcel">导出 Excel</el-button>
        </el-col>
      </el-row>
      <!-- 实时更新状态栏 -->
      <el-row v-if="realtimeActive" style="margin-top:8px">
        <el-col>
          <el-text type="success" size="small">
            <el-icon style="vertical-align:middle;margin-right:4px"><Loading /></el-icon>
            实时更新中，最后更新：{{ lastUpdateTime || '—' }}，已更新 {{ realtimeCount }} 次
          </el-text>
        </el-col>
      </el-row>
    </el-card>

    <!-- 数据表格 -->
    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="tableData"
        stripe border
        height="calc(100vh - 240px)"
        size="small"
        highlight-current-row
        :row-key="row => row.code + row.date"
      >
        <el-table-column prop="date"         label="信号日期"    width="100" fixed />
        <el-table-column prop="code"         label="代码"        width="88"  fixed>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openIndicators(row)">
              {{ row.code }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="name"         label="名称"        width="110" fixed show-overflow-tooltip />
        <el-table-column prop="signal_close" label="信号日收盘"  width="96">
          <template #default="{ row }">{{ row.signal_close ?? '—' }}</template>
        </el-table-column>

        <!-- 实时数据列（高亮显示） -->
        <el-table-column label="实时价格" width="96">
          <template #header>
            <span>实时价格</span>
            <el-tag v-if="realtimeActive" type="success" size="small" style="margin-left:4px">实时</el-tag>
          </template>
          <template #default="{ row }">
            <span :class="priceClass(row._rt_change_pct)">
              {{ row._rt_price != null ? row._rt_price.toFixed(2) : (row.current_price ?? '—') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="实时涨跌幅" width="100">
          <template #header>
            <span>实时涨跌幅</span>
            <el-tag v-if="realtimeActive" type="success" size="small" style="margin-left:4px">实时</el-tag>
          </template>
          <template #default="{ row }">
            <span :class="chgClass(row._rt_change_pct)">
              {{ row._rt_change_pct != null ? row._rt_change_pct.toFixed(2) + '%' : (row.today_change != null ? (+row.today_change).toFixed(2) + '%' : '—') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="成交量(手)" width="105">
          <template #header>
            <span>成交量(手)</span>
            <el-tag v-if="realtimeActive" type="success" size="small" style="margin-left:4px">实时</el-tag>
          </template>
          <template #default="{ row }">
            <span>{{ row._rt_volume != null ? fmtVol(row._rt_volume) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="成交额(元)" width="105">
          <template #header>
            <span>成交额(元)</span>
            <el-tag v-if="realtimeActive" type="success" size="small" style="margin-left:4px">实时</el-tag>
          </template>
          <template #default="{ row }">
            <span>{{ row._rt_amount != null ? fmtAmt(row._rt_amount) : '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="chg_from_signal" label="信号日至今涨跌" width="130">
          <template #default="{ row }">
            <!-- 实时开启后用实时价格重算 -->
            <span :class="chgClass(calcChgFromSignal(row))" style="font-weight:600">
              {{ calcChgFromSignal(row) != null ? calcChgFromSignal(row).toFixed(2) + '%' : '—' }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[100, 200, 500]"
          layout="total, sizes, prev, pager, next, jumper"
          background small
          @current-change="loadPage"
          @size-change="sz => { pageSize = sz; loadPage(1) }"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, RefreshRight, Download, VideoPlay, VideoPause, Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import * as XLSX from 'xlsx'
import { fetchTradeDate } from '@/api'

const route  = useRoute()
const router = useRouter()

const loading     = ref(false)
const tableData   = ref([])
const currentDate = ref('')
const searchText  = ref('')
const currentPage = ref(1)
const pageSize    = ref(200)
const total       = ref(0)

// 实时更新状态
const realtimeActive  = ref(false)
const lastUpdateTime  = ref('')
const realtimeCount   = ref(0)
let   realtimeTimer   = null
// 随机间隔 2500~3000ms
const nextInterval = () => 2500 + Math.random() * 500

const tableName = () => route.params.table
let searchTimer = null

// ── 初始化 ────────────────────────────────────────────────
async function init() {
  try {
    currentDate.value = await fetchTradeDate()
  } catch {
    currentDate.value = new Date().toISOString().slice(0, 10)
  }
  loadPage(1)
}

// ── 加载策略数据 ──────────────────────────────────────────
async function loadPage(page) {
  currentPage.value = page
  loading.value = true
  try {
    const params = { table: tableName(), date: currentDate.value, page, size: pageSize.value }
    if (searchText.value) params.search = searchText.value
    const res = await axios.get('/api/custom_strategy', { params })
    // 附加实时字段占位
    tableData.value = (res.data.data || []).map(r => ({
      ...r,
      _rt_price:      null,
      _rt_change_pct: null,
      _rt_volume:     null,
      _rt_amount:     null,
    }))
    total.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function onDateChange() { loadPage(1) }
function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadPage(1), 400)
}

// ── 实时更新 ──────────────────────────────────────────────
function toggleRealtime() {
  if (realtimeActive.value) {
    stopRealtime()
  } else {
    startRealtime()
  }
}

function startRealtime() {
  realtimeActive.value = true
  realtimeCount.value  = 0
  scheduleFetch()
}

function stopRealtime() {
  realtimeActive.value = false
  clearTimeout(realtimeTimer)
  realtimeTimer = null
}

function scheduleFetch() {
  if (!realtimeActive.value) return
  fetchRealtime().finally(() => {
    if (realtimeActive.value) {
      realtimeTimer = setTimeout(scheduleFetch, nextInterval())
    }
  })
}

async function fetchRealtime() {
  const rows = tableData.value
  if (!rows.length) return
  // 当前页所有代码（去重）
  const codes = [...new Set(rows.map(r => r.code))].join(',')
  try {
    const res = await axios.get('/api/sina_realtime', { params: { codes } })
    const rt = res.data  // { "000001": { price, change_pct, volume, amount }, ... }
    // 更新每行的实时字段（直接 mutate，Vue3 reactive 会追踪）
    rows.forEach(row => {
      const d = rt[row.code]
      if (d) {
        row._rt_price      = d.price
        row._rt_change_pct = d.change_pct
        row._rt_volume     = d.volume
        row._rt_amount     = d.amount
      }
    })
    realtimeCount.value++
    const now = new Date()
    lastUpdateTime.value = now.toLocaleTimeString('zh-CN', { hour12: false })
  } catch (e) {
    console.warn('实时行情获取失败:', e.message)
  }
}

// ── 计算"信号日至今涨跌"（实时开启时用实时价格重算）──────
function calcChgFromSignal(row) {
  const price = row._rt_price ?? row.current_price
  const base  = row.signal_close
  if (price == null || !base || base === 0) return row.chg_from_signal ?? null
  return +((price - base) / base * 100).toFixed(2)
}

// ── 辅助函数 ──────────────────────────────────────────────
function openIndicators(row) {
  router.push({ path: '/indicators', query: { code: row.code, date: currentDate.value, name: row.name } })
}

function chgClass(val) {
  if (val == null) return ''
  return +val > 0 ? 'up' : +val < 0 ? 'down' : ''
}
function priceClass(chgPct) {
  if (chgPct == null) return ''
  return +chgPct > 0 ? 'up' : +chgPct < 0 ? 'down' : ''
}
function fmtVol(v) {
  if (v == null) return '—'
  // v 单位: 手(100股)
  const n = Number(v)
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿手'
  if (n >= 1e4) return (n / 1e4).toFixed(2) + '万手'
  return n.toLocaleString() + '手'
}
function fmtAmt(v) {
  if (v == null) return '—'
  // v 单位: 千元，转为元显示
  const yuan = Number(v) * 1000
  if (yuan >= 1e8) return (yuan / 1e8).toFixed(2) + '亿'
  if (yuan >= 1e4) return (yuan / 1e4).toFixed(2) + '万'
  return yuan.toLocaleString() + '元'
}

function exportExcel() {
  const colMap = {
    date: '信号日期', code: '代码', name: '名称',
    signal_close: '信号日收盘',
    current_price: '当前价格', today_change: '今日涨跌幅(%)',
    chg_from_signal: '信号日至今涨跌(%)'
  }
  const rows = tableData.value.map(row => {
    const out = {}
    Object.keys(colMap).forEach(k => { out[colMap[k]] = row[k] ?? '' })
    return out
  })
  const ws = XLSX.utils.json_to_sheet(rows)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'data')
  XLSX.writeFile(wb, `${tableName()}_${currentDate.value}.xlsx`)
}

// ── 路由切换 / 销毁时停止实时更新 ─────────────────────────
watch(() => route.params.table, () => {
  stopRealtime()
  tableData.value   = []
  total.value       = 0
  currentPage.value = 1
  loadPage(1)
})

onBeforeUnmount(stopRealtime)
onMounted(init)
</script>

<style scoped>
.cs-page { display: flex; flex-direction: column; gap: 12px; }
.toolbar :deep(.el-card__body) { padding: 12px 16px; }
.table-card :deep(.el-card__body) { padding: 0; }
.pagination-bar {
  padding: 10px 16px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid #f0f0f0;
}
.up   { color: #f56c6c; font-weight: 500; }
.down { color: #67c23a; font-weight: 500; }
</style>
