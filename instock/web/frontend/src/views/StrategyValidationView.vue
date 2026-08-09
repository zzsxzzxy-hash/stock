<template>
  <div class="validation-wrap">
    <div class="page-header">
      <div>
        <div class="page-title">策略验证/胜率复盘</div>
        <div class="page-sub">
          {{ dateText }} 候选出现后延迟 {{ query.buyDelay }} 分钟模拟买入，验证当天与隔夜收益
        </div>
      </div>
      <div class="header-actions">
        <el-date-picker
          v-model="query.date"
          type="date"
          value-format="YYYY-MM-DD"
          style="width: 150px"
          placeholder="交易日期"
          @change="() => loadData()"
        />
        <el-input-number
          v-model="query.buyDelay"
          :min="0"
          :max="10"
          :step="1"
          controls-position="right"
          style="width: 118px"
          @change="() => loadData()"
        />
        <el-time-picker
          v-model="query.nextCutoff"
          format="HH:mm"
          value-format="HH:mm"
          style="width: 120px"
          @change="() => loadData()"
        />
        <el-button :icon="Refresh" :loading="loading" @click="loadData(true)">刷新</el-button>
      </div>
    </div>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-label">候选股票</div>
        <div class="metric-value">{{ summary.candidate_count ?? 0 }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">可定价</div>
        <div class="metric-value">{{ summary.priced_count ?? 0 }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">当天收盘胜率</div>
        <div class="metric-value">{{ fmtRate(summary.same_day?.win_rate) }}</div>
        <div class="metric-sub">均值 {{ fmtPct(summary.same_day?.avg) }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">次日{{ nextCutoffText }}胜率</div>
        <div class="metric-value">{{ fmtRate(summary.next_0940?.win_rate) }}</div>
        <div class="metric-sub">均值 {{ fmtPct(summary.next_0940?.avg) }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">次日{{ nextCutoffText }}前触盈</div>
        <div class="metric-value">{{ fmtRate(summary.next_0940_max?.win_rate) }}</div>
        <div class="metric-sub">最高均值 {{ fmtPct(summary.next_0940_max?.avg) }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">次日收盘胜率</div>
        <div class="metric-value">{{ fmtRate(summary.next_close?.win_rate) }}</div>
        <div class="metric-sub">均值 {{ fmtPct(summary.next_close?.avg) }}</div>
      </div>
    </div>

    <div class="experiment-panel" v-if="experimentInfo.strategy_id">
      <div class="experiment-head">
        <div>
          <div class="panel-title">{{ experimentInfo.strategy_name }}</div>
          <div class="panel-sub">版本 {{ experimentInfo.version }} · 与当前候选池基准组按同一买入与卖出时点回放</div>
        </div>
        <el-tag size="small" type="warning" effect="plain">实验验证中</el-tag>
      </div>
      <div class="experiment-rules">
        <span class="experiment-rule">原始候选池不删票</span>
        <span class="experiment-rule">涨幅加速至少4%</span>
        <span class="experiment-rule muted">频次仅作辅助观察</span>
      </div>
      <div class="experiment-metrics">
        <div>
          <span>回放交易日</span>
          <b>{{ experimentInfo.daily?.length ?? 0 }}</b>
        </div>
        <div>
          <span>基准样本</span>
          <b>{{ experimentInfo.baseline?.candidate_count ?? 0 }}</b>
        </div>
        <div>
          <span>爆发标签</span>
          <b>{{ experimentInfo.experiment?.candidate_count ?? 0 }}</b>
        </div>
        <div>
          <span>高收益机会</span>
          <b>{{ experimentInfo.opportunity?.total ?? 0 }}</b>
        </div>
        <div>
          <span>V1捕捉</span>
          <b>{{ experimentInfo.opportunity?.captured ?? 0 }}</b>
          <em :class="pnlClass(experimentCaptureRate)">{{ fmtRate(experimentCaptureRate) }}</em>
        </div>
      </div>
      <el-table :data="experimentInfo.daily || []" border size="small" max-height="214" class="experiment-table">
        <el-table-column prop="trade_date" label="交易日" width="102" />
        <el-table-column label="基准样本" width="86" align="center">
          <template #default="{ row }">{{ row.baseline?.candidate_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="高收益机会" width="100" align="center">
          <template #default="{ row }">{{ row.opportunity_total ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="爆发标签" width="92" align="center">
          <template #default="{ row }">{{ row.experiment?.candidate_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="V1捕捉" min-width="110" align="right">
          <template #default="{ row }">
            {{ row.opportunity_captured ?? 0 }} / {{ fmtRate(dailyCaptureRate(row)) }}
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="mainline-panel">
      <div class="mainline-row">
        <span class="mainline-label">10点前判定主线</span>
        <template v-if="mainlineInfo.judged_detail?.length">
          <span
            v-for="item in mainlineInfo.judged_detail"
            :key="`judged-${item.theme}`"
            class="mainline-chip judged"
            :title="`出现 ${item.appear_count} 次，股票 ${item.stock_count} 只，最高核心分 ${fmtNum(item.max_core_score, 1)}`"
          >
            {{ item.theme }}
          </span>
        </template>
        <span v-else class="empty-text">-</span>
      </div>
      <div class="mainline-row">
        <span class="mainline-label">收盘实际主线</span>
        <template v-if="mainlineInfo.actual_detail?.length">
          <span
            v-for="item in mainlineInfo.actual_detail"
            :key="`actual-${item.theme}`"
            class="mainline-chip actual"
            :title="`均涨 ${fmtPct(item.avg_ret)}，5%以上 ${item.ret5_count} 只，8%以上 ${item.ret8_count} 只`"
          >
            {{ item.theme }}
          </span>
        </template>
        <span v-else class="empty-text">-</span>
      </div>
    </div>

    <div class="limitup-panel">
      <div class="limitup-head">
        <div>
          <div class="panel-title">涨停板验证</div>
          <div class="panel-sub">
            涨停 {{ filteredLimitupSummary.limitup_count }} 只，候选池命中 {{ filteredLimitupSummary.in_pool_count }} 只，漏掉 {{ filteredLimitupSummary.missed_count }} 只
          </div>
        </div>
        <el-segmented v-model="limitupBoard" :options="limitupBoardOptions" class="limitup-board-filter" />
      </div>
      <div class="limitup-themes" v-if="filteredLimitupThemes.length">
        <span
          v-for="item in filteredLimitupThemes.slice(0, 8)"
          :key="item.trade_theme"
          class="limitup-theme"
          :title="item.sample_codes"
        >
          {{ item.trade_theme || '未归类' }}
          <b>{{ item.limitup_count }}</b>
          <em>命中{{ item.in_pool_count }}</em>
        </span>
      </div>
      <el-table
        v-if="filteredLimitupDetail.length"
        :data="filteredLimitupDetail"
        border
        size="small"
        max-height="260"
        row-key="code"
        class="limitup-table"
      >
        <el-table-column prop="code" label="代码" width="86" />
        <el-table-column prop="name" label="名称" width="104" />
        <el-table-column prop="board" label="市场" width="86" />
        <el-table-column prop="trade_theme" label="主线" min-width="140">
          <template #default="{ row }">
            <span class="theme-chip" :class="themeClass(row.trade_theme)">{{ row.trade_theme || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="quote_change" label="涨幅" width="82" align="right">
          <template #default="{ row }"><span class="text-up">{{ fmtPct(row.quote_change) }}</span></template>
        </el-table-column>
        <el-table-column prop="in_candidate_pool" label="在候选池" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.in_candidate_pool" size="small" type="success">是</el-tag>
            <el-tag v-else size="small" type="danger">否</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="recommend_count" label="次数" width="70" align="center" />
        <el-table-column label="原因" min-width="360" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.in_candidate_pool">{{ row.candidate_reason || '-' }}</span>
            <span v-else class="miss-text">{{ row.miss_reason || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="filter-row">
      <el-segmented v-model="filters.scope" :options="scopeOptions" @change="applyFilters" />
      <el-input
        v-model="filters.keyword"
        clearable
        style="width: 220px"
        placeholder="代码/名称/主线"
        @input="applyFilters"
      />
      <el-select
        v-model="filters.result"
        style="width: 132px"
        @change="applyFilters"
      >
        <el-option label="全部结果" value="all" />
        <el-option label="当天红" value="same_day_win" />
        <el-option label="次日红" value="next_win" />
        <el-option label="次日触盈" value="next_touch" />
        <el-option label="次日亏" value="next_loss" />
      </el-select>
    </div>

    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="pagedRows"
        border
        size="small"
        height="calc(100vh - 338px)"
        row-key="code"
      >
        <el-table-column prop="code" label="代码" width="86" fixed sortable />
        <el-table-column prop="name" label="名称" width="104" fixed sortable />
        <el-table-column prop="recommend_count" label="次数" width="78" align="center" sortable>
          <template #default="{ row }">
            <span
              class="count-pill"
              :class="countClass(row.recommend_count)"
              :title="row.recommend_snapshots || '-'"
            >
              {{ row.recommend_count }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="recommend_snapshots" label="出现时间点" min-width="190" show-overflow-tooltip />
        <el-table-column prop="first_snapshot" label="首次" width="72" sortable />
        <el-table-column prop="buy_time" label="模拟买入" width="92" sortable>
          <template #default="{ row }">{{ row.buy_actual_time || row.buy_time }}</template>
        </el-table-column>
        <el-table-column prop="buy_price" label="买入价" width="86" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.buy_price, 2) }}</template>
        </el-table-column>
        <el-table-column prop="same_day_return_pct" label="当天收盘" width="104" align="right" sortable>
          <template #default="{ row }"><span :class="pnlClass(row.same_day_return_pct)">{{ fmtPct(row.same_day_return_pct) }}</span></template>
        </el-table-column>
        <el-table-column prop="next_0940_return_pct" :label="`次日${nextCutoffText}`" width="106" align="right" sortable>
          <template #default="{ row }"><span :class="pnlClass(row.next_0940_return_pct)">{{ fmtPct(row.next_0940_return_pct) }}</span></template>
        </el-table-column>
        <el-table-column prop="next_0940_max_return_pct" :label="`次日${nextCutoffText}前最高`" width="136" align="right" sortable>
          <template #default="{ row }"><span :class="pnlClass(row.next_0940_max_return_pct)">{{ fmtPct(row.next_0940_max_return_pct) }}</span></template>
        </el-table-column>
        <el-table-column prop="next_close_return_pct" label="次日收盘" width="104" align="right" sortable>
          <template #default="{ row }"><span :class="pnlClass(row.next_close_return_pct)">{{ fmtPct(row.next_close_return_pct) }}</span></template>
        </el-table-column>
        <el-table-column prop="max_core_score" label="核心分" width="86" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.max_core_score, 1) }}</template>
        </el-table-column>
        <el-table-column prop="ever_buyable" label="可买" width="74" align="center" sortable>
          <template #default="{ row }">
            <el-tag v-if="row.ever_buyable" size="small" type="success">是</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="mainlines" label="主线" min-width="170">
          <template #default="{ row }">
            <div class="theme-cell">
              <span
                v-for="theme in splitThemes(row.mainlines)"
                :key="`${row.code}-${theme}`"
                class="theme-chip"
                :class="themeClass(theme)"
              >
                {{ theme }}
              </span>
              <span v-if="!splitThemes(row.mainlines).length">-</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="modes" label="模式" min-width="150" show-overflow-tooltip />
        <el-table-column prop="buy_statuses" label="买入状态" min-width="128" show-overflow-tooltip />
        <el-table-column prop="risk_tags" label="风险" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="risk-text">{{ row.risk_tags || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager-row">
        <span class="pager-info">共 {{ visibleRows.length }} 只</span>
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[50, 100, 200, 500]"
          layout="sizes, prev, pager, next"
          :total="visibleRows.length"
          small
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import axios from 'axios'

const loading = ref(false)
const rows = ref([])
const visibleRows = ref([])
const summary = ref({})
const meta = ref({})
const mainlineInfo = ref({})
const limitupInfo = ref({})
const experimentInfo = ref({})
const limitupBoard = ref('all')
const currentPage = ref(1)
const pageSize = ref(100)

const query = ref({
  date: '',
  buyDelay: 2,
  nextCutoff: '09:40',
})

const filters = ref({
  scope: 'all',
  keyword: '',
  result: 'all',
})

const scopeOptions = [
  { label: '全部', value: 'all' },
  { label: '只看可买', value: 'buyable' },
  { label: '次数>=5', value: 'freq5' },
  { label: '无风险', value: 'clean' },
]

const limitupBoardOptions = [
  { label: '全部市场', value: 'all' },
  { label: '沪深主板', value: 'main' },
  { label: '创业板', value: 'growth' },
  { label: '科创板', value: 'star' },
  { label: '京市A股', value: 'beijing' },
]

const dateText = computed(() => meta.value.date || query.value.date || '-')
const nextCutoffText = computed(() => query.value.nextCutoff || meta.value.next_cutoff || '09:40')
const experimentWinRateDelta = computed(() => {
  const base = Number(experimentInfo.value.baseline?.next_0940?.win_rate)
  const value = Number(experimentInfo.value.experiment?.next_0940?.win_rate)
  return Number.isFinite(base) && Number.isFinite(value) ? value - base : null
})
const experimentAvgDelta = computed(() => {
  const base = Number(experimentInfo.value.baseline?.next_0940?.avg)
  const value = Number(experimentInfo.value.experiment?.next_0940?.avg)
  return Number.isFinite(base) && Number.isFinite(value) ? value - base : null
})
const experimentCaptureRate = computed(() => {
  const total = Number(experimentInfo.value.opportunity?.total)
  const captured = Number(experimentInfo.value.opportunity?.captured)
  return total > 0 && Number.isFinite(captured) ? captured * 100 / total : null
})
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return visibleRows.value.slice(start, start + pageSize.value)
})

function limitupBoardGroup(board) {
  if (board === '沪市主板' || board === '深市主板') return 'main'
  if (board === '创业板') return 'growth'
  if (board === '科创板') return 'star'
  if (board === '北交所') return 'beijing'
  return 'other'
}

const filteredLimitupDetail = computed(() => {
  const detail = limitupInfo.value.detail || []
  if (limitupBoard.value === 'all') return detail
  return detail.filter((row) => limitupBoardGroup(row.board) === limitupBoard.value)
})

const filteredLimitupSummary = computed(() => {
  const detail = filteredLimitupDetail.value
  const inPool = detail.filter((row) => row.in_candidate_pool).length
  return {
    limitup_count: detail.length,
    in_pool_count: inPool,
    missed_count: detail.length - inPool,
  }
})

const filteredLimitupThemes = computed(() => {
  const groups = new Map()
  filteredLimitupDetail.value.forEach((row) => {
    const theme = row.trade_theme || '未归类'
    const current = groups.get(theme) || {
      trade_theme: theme,
      limitup_count: 0,
      in_pool_count: 0,
      sample_codes: [],
    }
    current.limitup_count += 1
    if (row.in_candidate_pool) current.in_pool_count += 1
    if (current.sample_codes.length < 6) current.sample_codes.push(`${row.code} ${row.name}`)
    groups.set(theme, current)
  })
  return [...groups.values()]
    .map((item) => ({ ...item, sample_codes: item.sample_codes.join(' / ') }))
    .sort((a, b) => b.limitup_count - a.limitup_count || b.in_pool_count - a.in_pool_count || a.trade_theme.localeCompare(b.trade_theme))
})

async function loadData(force = false) {
  loading.value = true
  try {
    const params = {
      buy_delay: query.value.buyDelay,
      next_cutoff: query.value.nextCutoff,
    }
    if (force) params.refresh = '1'
    if (query.value.date) params.date = query.value.date
    const [res, experimentRes] = await Promise.all([
      axios.get('/api/strategy_validation', { params }),
      axios.get('/api/strategy_validation_experiment', {
        params: { buy_delay: query.value.buyDelay, next_cutoff: query.value.nextCutoff },
      }),
    ])
    if (!res.data?.ok) throw new Error(res.data?.error || '加载失败')
    if (!experimentRes.data?.ok) throw new Error(experimentRes.data?.error || '实验数据加载失败')
    rows.value = res.data.data || []
    summary.value = res.data.summary || {}
    mainlineInfo.value = res.data.mainline_info || {}
    limitupInfo.value = res.data.limitup_info || {}
    experimentInfo.value = experimentRes.data || {}
    meta.value = {
      date: res.data.date,
      next_date: res.data.next_date,
      buy_delay: res.data.buy_delay,
      next_cutoff: res.data.next_cutoff,
    }
    if (!query.value.date) query.value.date = res.data.date
    applyFilters()
  } catch (err) {
    ElMessage.error(err?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  const keyword = String(filters.value.keyword || '').trim().toLowerCase()
  visibleRows.value = rows.value.filter((row) => {
    if (filters.value.scope === 'buyable' && !row.ever_buyable) return false
    if (filters.value.scope === 'freq5' && Number(row.recommend_count || 0) < 5) return false
    if (filters.value.scope === 'clean' && row.risk_tags) return false
    if (filters.value.result === 'same_day_win' && !(Number(row.same_day_return_pct) > 0)) return false
    if (filters.value.result === 'next_win' && !(Number(row.next_0940_return_pct) > 0)) return false
    if (filters.value.result === 'next_touch' && !(Number(row.next_0940_max_return_pct) > 0)) return false
    if (filters.value.result === 'next_loss' && !(Number(row.next_0940_return_pct) < 0)) return false
    if (!keyword) return true
    return [row.code, row.name, row.mainlines].some((v) => String(v || '').toLowerCase().includes(keyword))
  })
  currentPage.value = 1
}

function dailyCaptureRate(row) {
  const total = Number(row.opportunity_total)
  const captured = Number(row.opportunity_captured)
  return total > 0 && Number.isFinite(captured) ? captured * 100 / total : null
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return n.toFixed(digits)
}

function fmtPct(value) {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function fmtRate(value) {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${n.toFixed(1)}%`
}

function fmtDelta(value, unit = '') {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}${unit}`
}

function pnlClass(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  if (n > 0) return 'text-up'
  if (n < 0) return 'text-down'
  return ''
}

function countClass(value) {
  const n = Number(value)
  if (n >= 10) return 'count-strong'
  if (n >= 5) return 'count-mid'
  return 'count-low'
}

function splitThemes(value) {
  return String(value || '')
    .split('/')
    .map((item) => item.trim())
    .filter(Boolean)
}

function themeSet(list) {
  return new Set((list || []).map((item) => String(item.theme || '').trim()).filter(Boolean))
}

function themeClass(theme) {
  const actual = themeSet(mainlineInfo.value.actual_detail).has(theme)
  const judged = themeSet(mainlineInfo.value.judged_detail).has(theme)
  if (actual && judged) return 'theme-both'
  if (actual) return 'theme-actual'
  if (judged) return 'theme-judged'
  return ''
}

onMounted(loadData)
</script>

<style scoped>
.validation-wrap {
  color: #303133;
}
.page-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 12px;
}
.page-title {
  font-size: 18px;
  font-weight: 700;
}
.page-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.header-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.metric-card {
  min-height: 82px;
  padding: 14px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.metric-label {
  font-size: 12px;
  color: #909399;
}
.metric-value {
  margin-top: 8px;
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}
.metric-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.experiment-panel {
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid #f1d8a9;
  border-radius: 6px;
  background: #fffdf7;
}
.experiment-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.experiment-rules {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 10px 0;
}
.experiment-rule {
  padding: 3px 8px;
  border: 1px solid #e6c47c;
  border-radius: 4px;
  color: #9a6700;
  background: #fff;
  font-size: 12px;
}
.experiment-rule.muted {
  color: #909399;
  border-color: #dcdfe6;
}
.experiment-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(112px, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}
.experiment-metrics > div {
  min-height: 56px;
  padding-right: 10px;
  border-right: 1px solid #ebeef5;
}
.experiment-metrics > div:last-child {
  border-right: 0;
}
.experiment-metrics span,
.experiment-metrics em {
  display: block;
  font-size: 12px;
  color: #909399;
  font-style: normal;
}
.experiment-metrics b {
  display: inline-block;
  margin-top: 5px;
  font-size: 19px;
  color: #303133;
}
.experiment-metrics em {
  display: inline-block;
  margin-left: 5px;
}
.experiment-table {
  width: 100%;
}
.filter-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.limitup-panel {
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.limitup-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.panel-title {
  font-size: 15px;
  font-weight: 700;
}
.panel-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.limitup-themes {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 10px;
}
.limitup-theme {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid #ffd0d0;
  background: #fff7f7;
  color: #d03050;
  font-size: 12px;
}
.limitup-theme b {
  font-size: 13px;
}
.limitup-theme em {
  font-style: normal;
  color: #606266;
}
.limitup-table {
  width: 100%;
}
.miss-text {
  color: #d03050;
}
.mainline-panel {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.mainline-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.mainline-label {
  width: 112px;
  flex-shrink: 0;
  color: #606266;
  font-size: 12px;
  font-weight: 600;
}
.mainline-chip,
.theme-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid #e4e7ed;
  background: #f5f7fa;
  color: #606266;
}
.mainline-chip.judged,
.theme-chip.theme-judged {
  border-color: #95c8ff;
  background: #ecf5ff;
  color: #1d6ec1;
}
.mainline-chip.actual,
.theme-chip.theme-actual {
  border-color: #ffb3b3;
  background: #fff1f0;
  color: #d03050;
  font-weight: 700;
}
.theme-chip.theme-both {
  border-color: #409eff;
  background: #fff1f0;
  color: #d03050;
  font-weight: 700;
}
.theme-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.empty-text {
  color: #c0c4cc;
}
.table-card {
  border-radius: 6px;
}
.count-pill {
  display: inline-flex;
  min-width: 30px;
  height: 22px;
  align-items: center;
  justify-content: center;
  padding: 0 7px;
  border-radius: 4px;
  font-weight: 700;
}
.count-strong {
  background: #fff1f0;
  color: #d03050;
}
.count-mid {
  background: #fff7e6;
  color: #b7791f;
}
.count-low {
  background: #f4f4f5;
  color: #909399;
}
.text-up {
  color: #d03050;
  font-weight: 600;
}
.text-down {
  color: #059669;
  font-weight: 600;
}
.risk-text {
  color: #c45656;
}
.pager-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 10px;
}
.pager-info {
  font-size: 12px;
  color: #909399;
}

@media (max-width: 1280px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(140px, 1fr));
  }
}

@media (max-width: 760px) {
  .page-header,
  .filter-row {
    flex-direction: column;
    align-items: stretch;
  }
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
