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
            @change="loadPage(1)"
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
        <el-col :span="14" style="text-align:right">
          <el-text type="info" size="small" style="margin-right:12px">
            共 {{ total }} 条
          </el-text>
          <el-button :icon="RefreshRight" @click="loadPage(1)" :loading="loading">刷新</el-button>
          <el-button :icon="Download" @click="exportExcel">导出 Excel</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 数据表格 -->
    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="tableData"
        stripe border
        height="calc(100vh - 220px)"
        size="small"
        highlight-current-row
      >
        <el-table-column prop="date"         label="信号日期"  width="100" fixed />
        <el-table-column prop="code"         label="代码"      width="88"  fixed>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openIndicators(row)">
              {{ row.code }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="name"         label="名称"      width="110" fixed show-overflow-tooltip />
        <el-table-column prop="signal_close" label="信号日收盘" width="100">
          <template #default="{ row }">
            <span>{{ row.signal_close ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="current_price" label="当前价格" width="100">
          <template #default="{ row }">
            <span :class="priceClass(row.today_change)">
              {{ row.current_price ?? '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="today_change" label="今日涨跌幅" width="100">
          <template #default="{ row }">
            <span :class="chgClass(row.today_change)">
              {{ row.today_change != null ? (+row.today_change).toFixed(2) + '%' : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="chg_from_signal" label="信号日至今涨跌" width="130">
          <template #default="{ row }">
            <span :class="chgClass(row.chg_from_signal)" style="font-weight:600">
              {{ row.chg_from_signal != null ? (+row.chg_from_signal).toFixed(2) + '%' : '—' }}
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
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, RefreshRight, Download } from '@element-plus/icons-vue'
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

// 从路由参数取表名
const tableName = () => route.params.table

let searchTimer = null

async function init() {
  try {
    currentDate.value = await fetchTradeDate()
  } catch {
    currentDate.value = new Date().toISOString().slice(0, 10)
  }
  loadPage(1)
}

async function loadPage(page) {
  currentPage.value = page
  loading.value = true
  try {
    const params = {
      table:  tableName(),
      date:   currentDate.value,
      page:   page,
      size:   pageSize.value,
    }
    if (searchText.value) params.search = searchText.value

    const res = await axios.get('/api/custom_strategy', { params })
    tableData.value = res.data.data  || []
    total.value     = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadPage(1), 400)
}

function openIndicators(row) {
  router.push({
    path: '/indicators',
    query: { code: row.code, date: currentDate.value, name: row.name }
  })
}

function chgClass(val) {
  if (val == null) return ''
  return val > 0 ? 'up' : val < 0 ? 'down' : ''
}

function priceClass(todayChg) {
  if (todayChg == null) return ''
  return todayChg > 0 ? 'up' : todayChg < 0 ? 'down' : ''
}

function exportExcel() {
  const colMap = {
    date: '信号日期', code: '代码', name: '名称',
    signal_close: '信号日收盘', current_price: '当前价格',
    today_change: '今日涨跌幅(%)', chg_from_signal: '信号日至今涨跌(%)'
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

// 切换策略时重新加载
watch(() => route.params.table, () => {
  tableData.value = []
  total.value     = 0
  currentPage.value = 1
  loadPage(1)
})

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
