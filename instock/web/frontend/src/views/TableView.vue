<template>
  <div class="table-page">
    <!-- 工具栏 -->
    <el-card class="toolbar" shadow="never">
      <el-row :gutter="12" align="middle">
        <el-col :span="4">
          <el-date-picker
            v-model="currentDate"
            type="date"
            placeholder="选择日期"
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
            :placeholder="hasCode ? '搜索代码/名称' : '搜索名称'"
            clearable
            :prefix-icon="Search"
            @input="onSearch"
          />
        </el-col>
        <!-- 板块筛选（只对有 code 字段的表显示） -->
        <el-col :span="10" v-if="hasCode">
          <el-radio-group v-model="marketFilter" size="small" @change="onMarketChange">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="sh">沪市</el-radio-button>
            <el-radio-button value="sz">深市</el-radio-button>
            <el-radio-button value="cyb">创业板</el-radio-button>
            <el-radio-button value="bse">北交所</el-radio-button>
            <el-radio-button value="kcb">科创板</el-radio-button>
            <el-radio-button value="etf">ETF</el-radio-button>
          </el-radio-group>
        </el-col>
        <el-col :span="hasCode ? 5 : 15" style="text-align:right">
          <el-text type="info" size="small" style="margin-right:12px">
            共 {{ total }} 条
            <template v-if="attentionCount"> | 关注 {{ attentionCount }} 只</template>
          </el-text>
          <el-button :icon="RefreshRight" @click="loadPage(1)" :loading="loading">刷新</el-button>
          <el-button :icon="Download" @click="exportExcel">导出 Excel</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 数据表格 -->
    <el-card class="table-card" shadow="never">
      <el-table
        ref="tableRef"
        v-loading="loading"
        :data="tableData"
        stripe
        border
        height="calc(100vh - 220px)"
        highlight-current-row
        size="small"
      >
        <el-table-column prop="date" label="日期" width="96" fixed />
        <!-- 有 code 字段才渲染代码列 -->
        <el-table-column v-if="hasCode" prop="code" label="代码" width="88" fixed>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openIndicators(row)">
              {{ row.code }}
            </el-button>
          </template>
        </el-table-column>
        <!-- 有 name 字段才渲染名称列 -->
        <el-table-column v-if="hasName" prop="name" label="名称" :width="hasCode ? 120 : 150" fixed show-overflow-tooltip>
          <template #default="{ row }">
            <div style="display:flex;align-items:center;gap:4px;overflow:hidden">
              <span
                v-if="hasCode"
                :style="{ color: row.cdatetime ? '#e6a23c' : '#c0c4cc', cursor:'pointer', fontSize:'15px', flexShrink:0 }"
                @click="toggleAttentionLocal(row)"
              >{{ row.cdatetime ? '★' : '☆' }}</span>
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          v-for="col in dynamicColumns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width || 100"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span :class="cellClass(col.prop, row[col.prop])">
              {{ formatCell(col.prop, row[col.prop]) }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[100, 200, 500, 1000]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          small
          @current-change="loadPage"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, RefreshRight, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import * as XLSX from 'xlsx'
import { menuModules } from '@/config/menus'
import { fetchTradeDate, toggleAttention } from '@/api'

const route  = useRoute()
const router = useRouter()

const loading     = ref(false)
const tableData   = ref([])
const metaColumns = ref([])
const currentDate = ref('')
const searchText  = ref('')
const marketFilter = ref('')
const tableRef    = ref(null)
const currentPage = ref(1)
const pageSize    = ref(200)
const total       = ref(0)

// 搜索防抖 timer
let searchTimer = null

const tableName = computed(() => route.params.table)

const FIXED_COLS = new Set(['date', 'code', 'name', 'cdatetime'])
const dynamicColumns = computed(() =>
  metaColumns.value.filter(c => !FIXED_COLS.has(c.prop))
)

// 根据 meta 判断表是否含有 code / name 列
const hasCode = computed(() => metaColumns.value.some(c => c.prop === 'code'))
const hasName = computed(() => metaColumns.value.some(c => c.prop === 'name'))

const attentionCount = computed(() =>
  tableData.value.filter(r => r.cdatetime).length
)

// 加载元数据
async function loadMeta() {
  try {
    const res = await axios.get(`/api/meta?name=${tableName.value}`)
    metaColumns.value = res.data.columns || []
  } catch (e) {
    console.error('loadMeta failed', e)
  }
}

// 加载一页数据
async function loadPage(page = currentPage.value) {
  if (!currentDate.value) return
  loading.value = true
  currentPage.value = page
  try {
    const params = new URLSearchParams({
      name: tableName.value,
      date: currentDate.value,
      page: page,
      size: pageSize.value,
    })
    if (searchText.value)  params.set('search', searchText.value)
    if (marketFilter.value) params.set('market', marketFilter.value)

    const res = await axios.get(`/api/data?${params}`)
    tableData.value = res.data.data  || []
    total.value     = res.data.total || 0
  } catch (e) {
    ElMessage.error('数据加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

function onDateChange() { loadPage(1) }
function onSizeChange(size) { pageSize.value = size; loadPage(1) }
function onMarketChange() { loadPage(1) }

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadPage(1), 400)
}

async function initDate() {
  try {
    const res = await fetchTradeDate()
    currentDate.value = res.data.date
  } catch {
    currentDate.value = new Date().toISOString().slice(0, 10)
  }
}

async function toggleAttentionLocal(row) {
  const otype = row.cdatetime ? 'del' : 'add'
  try {
    await toggleAttention(row.code, otype)
    // 本地更新，不重新拉全量
    row.cdatetime = otype === 'add' ? new Date().toISOString() : null
    ElMessage.success(otype === 'add' ? '已添加关注' : '已取消关注')
  } catch {
    ElMessage.error('操作失败')
  }
}

function openIndicators(row) {
  router.push({ path: '/indicators', query: { code: row.code, date: currentDate.value, name: row.name } })
}

// 涨跌颜色
function cellClass(prop, val) {
  if (['change_rate', 'ups_downs', 'pct_chg'].includes(prop)) {
    const n = parseFloat(val)
    if (n > 0) return 'cell-up'
    if (n < 0) return 'cell-down'
  }
  return ''
}

function formatCell(prop, val) {
  if (val === null || val === undefined || val === '') return '-'
  const floatProps = ['change_rate', 'ups_downs', 'new_price', 'turnoverrate',
    'volume_ratio', 'open_price', 'high_price', 'low_price', 'pe', 'pb', 'amplitude']
  if (floatProps.includes(prop) && !isNaN(val)) return parseFloat(val).toFixed(2)
  if (['volume', 'deal_amount', 'total_market_cap', 'free_cap'].includes(prop)) {
    const n = Number(val)
    if (n >= 1e8)  return (n / 1e8).toFixed(2)  + '亿'
    if (n >= 1e4)  return (n / 1e4).toFixed(2)  + '万'
    return n.toLocaleString()
  }
  return val
}

// 导出 Excel（当前页）
function exportExcel() {
  // 构建 prop→label 映射（中文表头）
  const labelMap = {}
  metaColumns.value.forEach(c => { labelMap[c.prop] = c.label })
  // 固定列中文映射
  const fixedMap = { date: '日期', code: '代码', name: '名称', cdatetime: '关注时间' }
  const colMap = { ...fixedMap, ...labelMap }

  // 将数据中的 key 替换为中文
  const rows = tableData.value.map(row => {
    const out = {}
    Object.keys(row).forEach(k => {
      const label = colMap[k] || k
      out[label] = row[k]
    })
    return out
  })

  const ws = XLSX.utils.json_to_sheet(rows)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'data')
  XLSX.writeFile(wb, `${tableName.value}_${currentDate.value}_p${currentPage.value}.xlsx`)
}

// 切换表重新加载
watch(tableName, async () => {
  tableData.value    = []
  metaColumns.value  = []
  total.value        = 0
  currentPage.value  = 1
  marketFilter.value = ''
  searchText.value   = ''
  await loadMeta()
  await loadPage(1)
})

onMounted(async () => {
  // loadMeta 和 initDate 并行，节省一个 RTT
  await Promise.all([loadMeta(), initDate()])
  await loadPage(1)
})
</script>

<style scoped>
.table-page { display: flex; flex-direction: column; gap: 10px; height: 100%; }
.toolbar { flex-shrink: 0; }
.toolbar :deep(.el-card__body) { padding: 10px 16px; }
.table-card { flex: 1; min-height: 0; }
.table-card :deep(.el-card__body) { padding: 0; display: flex; flex-direction: column; }

.pagination-bar {
  padding: 8px 12px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}

.cell-up   { color: #f56c6c; font-weight: 600; }
.cell-down { color: #67c23a; font-weight: 600; }
</style>
