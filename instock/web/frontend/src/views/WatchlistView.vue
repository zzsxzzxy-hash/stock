<template>
  <div class="watchlist-page">
    <!-- 工具栏 -->
    <el-card shadow="never" class="toolbar">
      <el-row :gutter="12" align="middle">
        <el-col :span="6">
          <el-input
            v-model="search"
            placeholder="搜索代码/名称"
            clearable
            :prefix-icon="Search"
          />
        </el-col>
        <el-col :span="18" style="text-align:right">
          <el-text type="info" size="small" style="margin-right:12px">
            共关注 {{ filtered.length }} 只 | 行情日期：{{ latestDate || '—' }}
          </el-text>
          <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 空状态 -->
    <el-card v-if="!loading && list.length === 0" shadow="never">
      <el-empty description="暂无关注股票，在每日股票数据页点击 ☆ 即可添加" />
    </el-card>

    <!-- 关注列表表格 -->
    <el-card v-else shadow="never" class="table-card">
      <el-table
        :data="filtered"
        v-loading="loading"
        stripe
        border
        size="small"
        height="calc(100vh - 200px)"
        highlight-current-row
      >
        <!-- 代码 -->
        <el-table-column prop="code" label="代码" width="90" fixed>
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openIndicators(row)">
              {{ row.code }}
            </el-button>
          </template>
        </el-table-column>

        <!-- 名称 + 取消关注 -->
        <el-table-column prop="name" label="名称" width="110" fixed>
          <template #default="{ row }">
            <div style="display:flex;align-items:center;gap:6px">
              <el-tooltip content="取消关注" placement="top">
                <span
                  style="color:#e6a23c;cursor:pointer;font-size:15px;line-height:1"
                  @click="removeWatch(row)"
                >★</span>
              </el-tooltip>
              <span>{{ row.name || '—' }}</span>
            </div>
          </template>
        </el-table-column>

        <!-- 最新价 -->
        <el-table-column prop="new_price" label="最新价" width="80" align="right">
          <template #default="{ row }">
            <span :class="priceClass(row.change_rate)">
              {{ fmt2(row.new_price) }}
            </span>
          </template>
        </el-table-column>

        <!-- 涨跌幅 -->
        <el-table-column prop="change_rate" label="涨跌幅%" width="84" align="right">
          <template #default="{ row }">
            <span :class="priceClass(row.change_rate)">
              {{ fmt2(row.change_rate) }}%
            </span>
          </template>
        </el-table-column>

        <!-- 今开 -->
        <el-table-column prop="open_price" label="今开" width="78" align="right">
          <template #default="{ row }">{{ fmt2(row.open_price) }}</template>
        </el-table-column>

        <!-- 最高 -->
        <el-table-column prop="high_price" label="最高" width="78" align="right">
          <template #default="{ row }">
            <span class="cell-up">{{ fmt2(row.high_price) }}</span>
          </template>
        </el-table-column>

        <!-- 最低 -->
        <el-table-column prop="low_price" label="最低" width="78" align="right">
          <template #default="{ row }">
            <span class="cell-down">{{ fmt2(row.low_price) }}</span>
          </template>
        </el-table-column>

        <!-- 昨收 -->
        <el-table-column prop="pre_close_price" label="昨收" width="78" align="right">
          <template #default="{ row }">{{ fmt2(row.pre_close_price) }}</template>
        </el-table-column>

        <!-- 振幅 -->
        <el-table-column prop="amplitude" label="振幅%" width="78" align="right">
          <template #default="{ row }">{{ fmt2(row.amplitude) }}</template>
        </el-table-column>

        <!-- 换手率 -->
        <el-table-column prop="turnoverrate" label="换手率%" width="84" align="right">
          <template #default="{ row }">{{ fmt2(row.turnoverrate) }}</template>
        </el-table-column>

        <!-- 成交量 -->
        <el-table-column prop="volume" label="成交量(手)" width="100" align="right">
          <template #default="{ row }">{{ fmtVol(row.volume) }}</template>
        </el-table-column>

        <!-- 成交额 -->
        <el-table-column prop="deal_amount" label="成交额(元)" width="110" align="right">
          <template #default="{ row }">{{ fmtAmt(row.deal_amount) }}</template>
        </el-table-column>

        <!-- 关注时间 -->
        <el-table-column prop="datetime" label="关注时间" width="150" align="center">
          <template #default="{ row }">
            <el-text type="info" size="small">{{ fmtDatetime(row.datetime) }}</el-text>
          </template>
        </el-table-column>

        <!-- 操作 -->
        <el-table-column label="操作" width="90" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openIndicators(row)">K线</el-button>
            <el-button size="small" type="danger"  link @click="removeWatch(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const router = useRouter()

const loading    = ref(false)
const list       = ref([])
const latestDate = ref('')
const search     = ref('')

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter(r =>
    (r.code || '').includes(kw) || (r.name || '').toLowerCase().includes(kw)
  )
})

async function load() {
  loading.value = true
  try {
    const res = await axios.get('/api/watchlist')
    list.value       = res.data.data || []
    latestDate.value = res.data.latest_date || ''
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function removeWatch(row) {
  try {
    await ElMessageBox.confirm(
      `确定取消关注 ${row.code} ${row.name || ''} ？`,
      '取消关注',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await axios.delete(`/api/watchlist?code=${row.code}`)
    list.value = list.value.filter(r => r.code !== row.code)
    ElMessage.success('已取消关注')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('操作失败')
  }
}

function openIndicators(row) {
  router.push({
    path: '/indicators',
    query: { code: row.code, date: latestDate.value, name: row.name || '' }
  })
}

// ── 格式化 ─────────────────────────────────────────────────────────────────
function fmt2(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = parseFloat(v)
  return isNaN(n) ? '—' : n.toFixed(2)
}

function fmtVol(v) {
  const n = Number(v)
  if (!v || isNaN(n)) return '—'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toString()
}

function fmtAmt(v) {
  const n = Number(v)
  if (!v || isNaN(n)) return '—'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toString()
}

function fmtDatetime(v) {
  if (!v) return '—'
  return String(v).slice(0, 16).replace('T', ' ')
}

function priceClass(v) {
  const n = parseFloat(v)
  if (n > 0) return 'cell-up'
  if (n < 0) return 'cell-down'
  return ''
}

onMounted(load)
</script>

<style scoped>
.watchlist-page { display: flex; flex-direction: column; gap: 12px; }
.toolbar :deep(.el-card__body) { padding: 10px 16px; }
.table-card :deep(.el-card__body) { padding: 0; }

.cell-up   { color: #ef5350; font-weight: 500; }
.cell-down { color: #26a69a; font-weight: 500; }
</style>
