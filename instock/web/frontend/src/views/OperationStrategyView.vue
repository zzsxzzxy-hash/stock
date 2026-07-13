<template>
  <div class="strategy-wrap">
    <div class="page-header">
      <div>
        <div class="page-title">超短主线接力</div>
        <div class="page-sub">{{ summaryText }}</div>
      </div>
      <div class="header-actions">
        <el-date-picker
          v-model="query.date"
          type="date"
          value-format="YYYY-MM-DD"
          style="width: 145px"
          placeholder="交易日期"
          @change="loadMainline(true)"
        />
        <el-time-picker
          v-model="query.snapshot"
          format="HH:mm"
          value-format="HH:mm"
          style="width: 110px"
          placeholder="快照"
          @change="loadMainline(true)"
        />
        <el-select
          v-model="query.markets"
          multiple
          collapse-tags
          collapse-tags-tooltip
          style="width: 210px"
          placeholder="市场"
          @change="loadMainline(true)"
        >
          <el-option
            v-for="item in marketOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select
          v-model="query.buyStatus"
          style="width: 128px"
          placeholder="买入状态"
        >
          <el-option
            v-for="item in buyStatusOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-checkbox v-model="query.highProbOnly" @change="loadMainline(true)">
          只看高概率
        </el-checkbox>
        <el-button :icon="Refresh" :loading="loading" @click="loadMainline(true)">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="12" class="metric-row">
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-label">主线状态</div>
          <div class="metric-value">
            <el-tag :type="statusType(summary.status)" effect="dark">{{ summary.status || '-' }}</el-tag>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-label">第一主线</div>
          <div class="metric-value strong">{{ summary.primary || '-' }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-label">候选主线</div>
          <div class="metric-value">{{ themeGroups.length }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-label">核心标的</div>
          <div class="metric-value">{{ candidateCount }}</div>
        </div>
      </el-col>
    </el-row>

    <div v-if="marketEnv.status" class="market-gate" :class="marketGateClass">
      <div class="gate-main">
        <el-tag :type="marketGateType" effect="dark">{{ marketEnv.status }}</el-tag>
        <strong>{{ marketEnv.action }}</strong>
        <span>{{ marketEnv.reason }}</span>
      </div>
      <div class="gate-metrics">
        <span>所选口径 {{ marketEnv.snapshot || meta.snapshot || '-' }}</span>
        <span>全A上涨 {{ fmtPct(envAll.up_rate) }}</span>
        <span>交易池上涨 {{ fmtPct(envPool.up_rate) }}</span>
        <span>交易池中位 {{ fmtPct(envPool.median_pct) }}</span>
        <span>跌超2% {{ envPool.down2_count ?? '-' }}</span>
        <span>高点回撤中位 {{ fmtPct(envPullback.median_pullback) }}</span>
        <span>回撤超2% {{ fmtPct(envPullback.pullback2_rate) }}</span>
      </div>
    </div>

    <el-card shadow="never" class="rule-panel">
      <div class="rule-grid">
        <div v-for="item in guardRules" :key="item.title" class="rule-item">
          <div class="rule-title">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </div>
          <div class="rule-lines">
            <el-tag
              v-for="line in item.lines"
              :key="line"
              size="small"
              effect="plain"
              :type="item.type"
            >
              {{ line }}
            </el-tag>
          </div>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="table-card">
      <template #header>
        <div class="card-header">
          <span>主线候选</span>
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="themes">按主线</el-radio-button>
            <el-radio-button value="stocks">按个股</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <template v-if="viewMode === 'themes'">
        <el-table
          v-loading="loading"
          :data="themeGroups"
          border
          size="small"
          row-key="theme"
          class="main-table"
        >
          <el-table-column prop="rank" label="#" width="56" align="center" sortable />
          <el-table-column prop="theme" label="主线" min-width="150" sortable />
          <el-table-column prop="status" label="状态" width="96" align="center" sortable>
            <template #default="{ row }">
              <el-tag :type="themeStatusType(row.status)" size="small">{{ row.status || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="score" label="强度" width="90" align="right" sortable />
          <el-table-column prop="candidate_count" label="候选" width="72" align="right" sortable />
          <el-table-column prop="ret5_count" label="5%+" width="72" align="right" sortable />
          <el-table-column prop="ret8_count" label="8%+" width="72" align="right" sortable />
          <el-table-column prop="top3_avg_ret" label="前3均涨" width="96" align="right" sortable>
            <template #default="{ row }">
              <span :class="toneClass(row.top3_avg_ret)">{{ fmtPct(row.top3_avg_ret) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="前排标的" min-width="260">
            <template #default="{ row }">
              <div class="stock-chips">
                <el-tag
                  v-for="stock in (row.stocks || []).slice(0, 5)"
                  :key="stock.code"
                  size="small"
                  effect="plain"
                  @click="recordStock(stock, row.theme)"
                >
                  {{ stock.code }} {{ stock.name }}
                </el-tag>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template v-else>
        <el-table
          v-loading="loading"
          :data="stockRows"
          border
          size="small"
          row-key="code"
          class="main-table"
        >
          <el-table-column prop="code" label="代码" width="86" fixed sortable />
          <el-table-column prop="name" label="名称" width="110" fixed sortable />
          <el-table-column prop="mainline_theme" label="主线" min-width="140" sortable>
            <template #default="{ row }">{{ row.mainline_theme || row.trade_theme || row.best_sector || '-' }}</template>
          </el-table-column>
          <el-table-column prop="score" label="核心分" width="86" align="right" sortable />
          <el-table-column prop="trade_mode" label="模式" width="126" sortable>
            <template #default="{ row }">
              <el-tag size="small" :type="modeType(row.trade_mode)">{{ row.trade_mode || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="observe_label" label="买入状态" width="92" align="center" sortable>
            <template #default="{ row }">
              <el-tag size="small" :type="observeType(row.observe_label)">{{ row.observe_label || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="ret_vs_prevclose" label="涨幅" width="86" align="right" sortable>
            <template #default="{ row }">
              <span :class="toneClass(row.ret_vs_prevclose)">{{ fmtPct(row.ret_vs_prevclose) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="pullback" label="回落" width="86" align="right" sortable>
            <template #default="{ row }">{{ fmtPct(row.pullback) }}</template>
          </el-table-column>
          <el-table-column prop="current_change_pct" label="实时涨跌幅" width="112" align="right" sortable>
            <template #default="{ row }">
              <span :class="toneClass(row.current_change_pct)">{{ fmtPct(row.current_change_pct) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="amt_vs_prev" label="量比" width="76" align="right" sortable>
            <template #default="{ row }">{{ fmtNum(row.amt_vs_prev, 2) }}</template>
          </el-table-column>
          <el-table-column prop="prob_label" label="胜率" width="100" align="center" sortable>
            <template #default="{ row }">
              <el-tag
                v-if="row.prob_label"
                :type="probTagType(row.prob_color)"
                size="small"
                :title="row.prob_tip"
              >
                {{ row.prob_icon }} {{ probLabelText(row.prob_label) }}
              </el-tag>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="turnoverrate" label="换手率" width="86" align="right" sortable>
            <template #default="{ row }">{{ fmtPct(row.turnoverrate) }}</template>
          </el-table-column>
          <el-table-column label="风险" min-width="180" sortable :sort-method="sortRisk">
            <template #default="{ row }">
              <span class="risk-text">{{ riskText(row) || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="108" fixed="right" align="center">
            <template #default="{ row }">
              <el-button size="small" :icon="EditPen" @click="recordStock(row)">记录</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { EditPen, Refresh } from '@element-plus/icons-vue'
import axios from 'axios'

const router = useRouter()
const loading = ref(false)
const viewMode = ref('themes')
const query = ref({
  date: '',
  snapshot: '09:45',
  markets: ['cyb', 'kcb', 'bj'],
  buyStatus: 'buyable',
  highProbOnly: false,  // 新增：只看高概率
})
const meta = ref({})
const summary = ref({})
const marketEnv = ref({})
const rawThemeGroups = ref([])
const rows = ref([])

const summaryText = computed(() => summary.value?.sentence || `${meta.value.date || ''} ${meta.value.snapshot || ''}`)
const envAll = computed(() => marketEnv.value?.all || {})
const envPool = computed(() => marketEnv.value?.pool || {})
const envPullback = computed(() => marketEnv.value?.pool_pullback || {})
const marketGateType = computed(() => marketEnv.value?.severity || 'info')
const marketGateClass = computed(() => `gate-${marketEnv.value?.severity || 'info'}`)
const themeGroups = computed(() => rawThemeGroups.value
  .map(group => {
    const stocks = (group.stocks || []).filter(matchBuyStatus)
    return { ...group, stocks, candidate_count: stocks.length }
  })
  .filter(group => group.stocks.length)
)
const candidateCount = computed(() => themeGroups.value.reduce((sum, group) => sum + Number(group.candidate_count || 0), 0))
const stockRows = computed(() => rows.value.filter(matchBuyStatus))

const marketOptions = [
  { label: '创业板', value: 'cyb' },
  { label: '科创板', value: 'kcb' },
  { label: '京市A股', value: 'bj' },
]

const buyStatusOptions = [
  { label: '可买', value: 'buyable' },
  { label: '等确认', value: 'confirm' },
  { label: '只观察', value: 'watch' },
  { label: '全部', value: 'all' },
]

const guardRules = [
  {
    title: '买点拦截',
    icon: 'Warning',
    type: 'danger',
    lines: ['急拉不追', '分歧不抢', '后排不接'],
  },
  {
    title: '主线确认',
    icon: 'DataLine',
    type: 'success',
    lines: ['多分支共振', '前排继续加强', '候选不分散'],
  },
  {
    title: '次日复审',
    icon: 'Clock',
    type: 'warning',
    lines: ['低开弱转卖', '卡位弱转换', '加强才持有'],
  },
  {
    title: '换股纪律',
    icon: 'Switch',
    type: 'info',
    lines: ['新主线更强', '新票是前排', '买点不过热'],
  },
]

async function loadMainline(force = false) {
  loading.value = true
  try {
    const params = {
      max_sector_rank: 8,
      min_sector_strong: 0,
      min_ret: -3,
      max_ret: 35,
      min_amt_ratio: 0.3,
      min_amount: 5000000,
      theme: 'auto',
      market: query.value.markets?.length ? query.value.markets.join(',') : 'all',
      limit: 300,
      include_bars: 0,
      high_prob_only: query.value.highProbOnly ? '1' : '0',  // 新增
    }
    if (query.value.date) params.date = query.value.date
    if (query.value.snapshot) params.snapshot = query.value.snapshot
    if (force) params.refresh = '1'
    const res = await axios.get('/api/mainline_core', { params, timeout: 90000 })
    summary.value = res.data.summary || {}
    marketEnv.value = res.data.market_env || {}
    rawThemeGroups.value = res.data.themes || []
    rows.value = res.data.data || []
    meta.value = {
      date: res.data.date,
      snapshot: res.data.snapshot,
      latest_time: res.data.latest_time,
    }
    if (!query.value.date) query.value.date = res.data.date || ''
    if (!query.value.snapshot) query.value.snapshot = res.data.snapshot || ''
  } catch (e) {
    ElMessage.error('主线数据加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function recordStock(row, theme = '') {
  const signalRisk = riskText(row)
  const signalCoreScore = row.core_score ?? row.score ?? ''
  const signalAmountRatio = row.amt_vs_prev ?? ''
  const systemJudgment = [
    `核心分：${formatSignalNumber(signalCoreScore, 1)}`,
    `模式：${row.trade_mode || '-'}`,
    `买入状态：${row.observe_label || '-'}`,
    `量比：${formatSignalNumber(signalAmountRatio, 2)}`,
    `风险：${signalRisk || '无'}`,
  ].join('；')
  router.push({
    path: '/operation_journal',
    query: {
      code: row.code,
      name: row.name,
      mainline: theme || row.mainline_theme || row.trade_theme || row.best_sector || '',
      action: 'buy',
      date: meta.value.date || query.value.date,
      time: meta.value.snapshot || query.value.snapshot,
      system_judgment: systemJudgment,
      signal_strategy: 'mainline_core',
      signal_snapshot_time: meta.value.snapshot || query.value.snapshot,
      signal_core_score: signalCoreScore,
      signal_mode: row.trade_mode || '',
      signal_buy_status: row.observe_label || '',
      signal_amount_ratio: signalAmountRatio,
      signal_risk: signalRisk,
    },
  })
}

function formatSignalNumber(value, digits) {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : String(value)
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '-'
  return `${fmtNum(v, 2)}%`
}

function toneClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'text-up' : 'text-down'
}

function statusType(v) {
  if (v === '主线明确') return 'danger'
  if (v === '主线发酵') return 'warning'
  if (v === '主线分散') return 'info'
  return 'info'
}

function themeStatusType(v) {
  if (v === '主线确认') return 'danger'
  if (v === '发酵中') return 'warning'
  if (v === '分歧中') return 'info'
  return 'info'
}

function modeType(v) {
  if (v === '主线核心追强') return 'danger'
  if (v === '核心中位承接') return 'warning'
  if (v === '主线低位突破') return 'success'
  return 'info'
}

function observeType(v) {
  if (v === '可追强' || v === '可买') return 'danger'
  if (v === '重点观察') return 'warning'
  if (v === '等确认') return 'success'
  if (v === '等回踩') return 'warning'
  if (v === '偏追高') return 'danger'
  return 'info'
}

function matchBuyStatus(row) {
  const label = row.observe_label || ''
  const risks = Array.isArray(row.risk_tags) ? row.risk_tags : []
  if (query.value.buyStatus === 'all') return true
  if (query.value.buyStatus === 'buyable') {
    if (marketEnv.value?.trade_allowed === false) return false
    if (marketEnv.value?.trade_allowed === 'guarded') {
      return ['可追强', '可买'].includes(label) && risks.length === 0
    }
    return ['可追强', '可买'].includes(label) || (label === '重点观察' && risks.length === 0)
  }
  if (query.value.buyStatus === 'confirm') {
    return ['等确认', '等回踩'].includes(label)
  }
  if (query.value.buyStatus === 'watch') {
    return ['只观察', '偏追高'].includes(label)
  }
  return true
}

function riskText(row) {
  if (Array.isArray(row.risk_tags) && row.risk_tags.length) return row.risk_tags.join(' / ')
  return String(row.tags || '').split(',').filter(Boolean).slice(0, 3).join(' / ')
}

function sortRisk(a, b) {
  return riskText(a).localeCompare(riskText(b), 'zh-Hans-CN')
}

function probTagType(color) {
  if (color === 'green') return 'success'
  if (color === 'red') return 'danger'
  if (color === 'yellow') return 'warning'
  return 'info'
}

function probLabelText(label) {
  if (label === 'high') return '高'
  if (label === 'low') return '低'
  return '中'
}

onMounted(() => loadMainline(false))
</script>

<style scoped>
.strategy-wrap { padding: 16px; }
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.page-title { font-size: 18px; font-weight: 700; color: #303133; }
.page-sub { margin-top: 4px; font-size: 12px; color: #909399; }
.header-actions { display: flex; align-items: center; gap: 8px; }
.metric-row { margin-bottom: 12px; }
.metric-card {
  height: 74px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.metric-label { font-size: 12px; color: #909399; }
.metric-value { margin-top: 8px; font-size: 22px; font-weight: 700; color: #303133; }
.metric-value.strong { font-size: 18px; color: #d9480f; }
.market-gate {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fff;
}
.gate-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.gate-main strong { color: #303133; white-space: nowrap; }
.gate-main span:last-child {
  color: #606266;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gate-metrics {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  color: #606266;
  font-size: 12px;
}
.gate-metrics span {
  padding: 3px 7px;
  background: #f5f7fa;
  border-radius: 4px;
  white-space: nowrap;
}
.gate-danger { border-color: #fecdca; background: #fff5f5; }
.gate-warning { border-color: #f8d9a7; background: #fff9ed; }
.gate-success { border-color: #c8e7b8; background: #f5fbf1; }
.rule-panel { margin-bottom: 12px; }
.rule-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.rule-item { min-height: 72px; border-right: 1px solid #ebeef5; padding-right: 12px; }
.rule-item:last-child { border-right: none; }
.rule-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 10px;
}
.rule-lines { display: flex; flex-wrap: wrap; gap: 6px; }
.table-card :deep(.el-card__header) { padding: 10px 12px; }
.card-header { display: flex; align-items: center; justify-content: space-between; font-weight: 700; }
.main-table { width: 100%; }
.stock-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.stock-chips .el-tag { cursor: pointer; }
.text-up { color: #d03050; font-weight: 600; }
.text-down { color: #059669; font-weight: 600; }
.risk-text { color: #c45656; font-size: 12px; }

@media (max-width: 1180px) {
  .rule-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .page-header { flex-direction: column; }
  .market-gate { align-items: flex-start; flex-direction: column; }
  .gate-metrics { justify-content: flex-start; }
}
</style>
