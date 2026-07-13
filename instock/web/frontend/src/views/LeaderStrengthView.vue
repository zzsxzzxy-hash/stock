<template>
  <div class="leader-page">
    <div class="toolbar">
      <div class="toolbar-left">
        <el-radio-group v-model="activeMode" size="small" @change="switchMode">
          <el-radio-button label="strict">严格入场</el-radio-button>
          <el-radio-button label="mainline">主线核心观察</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="queryDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="最新交易日"
          size="small"
          style="width: 140px"
          @change="fetchList(true)"
        />
        <el-time-picker
          v-model="snapshotTime"
          format="HH:mm"
          value-format="HH:mm"
          placeholder="最新分钟"
          clearable
          size="small"
          style="width: 118px"
          @change="onSnapshotChange"
          @clear="clearSnapshot"
        />
        <el-input
          v-model="keyword"
          placeholder="代码/名称/交易主线"
          clearable
          size="small"
          style="width: 180px"
        />
        <el-select
          v-if="activeMode === 'mainline'"
          v-model="mainlineTheme"
          size="small"
          style="width: 130px"
          @change="fetchList(true)"
        >
          <el-option label="自动主线" value="auto" />
          <el-option label="机器人" value="机器人" />
          <el-option label="玻璃基板" value="玻璃基板" />
          <el-option label="科技半导体" value="科技半导体" />
          <el-option label="医药生物" value="医药生物" />
          <el-option label="军工制造" value="军工高端制造" />
          <el-option label="新能源电力" value="新能源电力" />
          <el-option label="全部主线" value="全部" />
        </el-select>
      </div>
      <div class="toolbar-right">
        <el-text type="info" size="small">
          {{ meta.date || '—' }} {{ meta.snapshot || '—' }}，{{ modeTitle }} {{ filteredRows.length }} / {{ rows.length }} 只
          <template v-if="activeMode === 'mainline' && meta.list_mode === 'review'">
            ，观察池 {{ watchVisibleCount }} 只
          </template>
        </el-text>
        <el-button :icon="Refresh" size="small" :loading="loading" @click="fetchList(true)">刷新</el-button>
        <el-button
          :type="autoRefresh ? 'danger' : 'success'"
          :icon="autoRefresh ? VideoPause : VideoPlay"
          size="small"
          @click="toggleAutoRefresh"
        >
          {{ autoRefresh ? '停止实时刷新' : '实时刷新' }}
        </el-button>
      </div>
    </div>

    <div class="content-grid">
      <aside class="filters">
        <div class="filter-block">
          <div class="filter-title">龙头约束</div>
          <label>主线排名前 {{ maxSectorRank }}</label>
          <el-slider v-model="maxSectorRank" :min="1" :max="activeMode === 'mainline' ? 12 : 8" :step="1" @change="fetchList(true)" />
          <label>主线共振 {{ minSectorStrong }} 只以上</label>
          <el-slider v-model="minSectorStrong" :min="activeMode === 'mainline' ? 0 : 1" :max="activeMode === 'mainline' ? 20 : 8" :step="1" @change="fetchList(true)" />
        </div>

        <div class="filter-block">
          <div class="filter-title">显示控制</div>
          <template v-if="activeMode === 'strict'">
            <label>最低分 {{ minScore }}</label>
            <el-slider v-model="minScore" :min="60" :max="120" :step="1" @change="fetchList(true)" />
          </template>
          <label>最多返回 {{ limit }} 只</label>
          <el-slider v-model="limit" :min="20" :max="activeMode === 'mainline' ? 500 : 200" :step="20" @change="fetchList(true)" />
        </div>

        <div class="filter-block">
          <div class="filter-title">市场</div>
          <el-checkbox-group v-model="marketFilter" class="market-list">
            <el-checkbox label="sh">沪主板</el-checkbox>
            <el-checkbox label="sz">深主板</el-checkbox>
            <el-checkbox label="cyb">创业板</el-checkbox>
            <el-checkbox label="kcb">科创板</el-checkbox>
            <el-checkbox label="bj">北交所</el-checkbox>
          </el-checkbox-group>
        </div>

        <div class="filter-block stats">
          <div class="filter-title">结果概览</div>
          <div><span>最新入库</span><b>{{ meta.latest_time || '—' }}</b></div>
          <div><span>平均分</span><b>{{ avgScore }}</b></div>
          <div><span>主线数</span><b>{{ sectorCount }}</b></div>
          <div v-if="activeMode === 'mainline'"><span>观察池</span><b>{{ watchVisibleCount || '—' }}</b></div>
        </div>
      </aside>

      <main class="result-list" v-loading="loading">
        <template v-if="activeMode === 'mainline'">
          <section v-if="mainlineSummary?.sentence" class="market-summary">
            <div class="summary-main">
              <el-tag size="small" :type="summaryTone(mainlineSummary.status)">
                {{ mainlineSummary.status || '主线判断' }}
              </el-tag>
              <span>{{ mainlineSummary.sentence }}</span>
            </div>
            <div class="summary-tags">
              <el-tag
                v-for="item in summaryTopBroad"
                :key="item.name"
                size="small"
                effect="plain"
              >
                {{ item.name }} {{ fmtNum(item.score, 0) }}
              </el-tag>
            </div>
          </section>

          <el-empty
            v-if="!loading && !filteredThemeGroups.length && !filteredWatchThemeGroups.length"
            description="暂无主线核心观察"
          />

          <StockSignalDetail
            v-if="selectedDetail"
            v-loading="detailLoading"
            :row="selectedDetail"
            :date="meta.date"
            active-mode="mainline"
            editable-theme
            clickable-code
            @open-indicators="openIndicators"
            @load-theme-options="loadDominantOptions"
            @save-theme="saveTradeTheme"
          />

          <section
            v-for="theme in filteredThemeGroups"
            :key="theme.theme"
            class="theme-section"
          >
            <header class="theme-head">
              <div>
                <div class="theme-title">
                  <span class="theme-rank">#{{ theme.rank }}</span>
                  <b>{{ theme.theme }}</b>
                  <el-tag size="small" :type="themeStatusTone(theme.status)">{{ theme.status }}</el-tag>
                </div>
                <div class="theme-sub">
                  强度 {{ fmtNum(theme.score, 1) }}，共振 {{ theme.ret2_count }} 只，
                  <el-tooltip
                    placement="top"
                    :disabled="!(theme.ret5_stocks || []).length"
                    popper-class="strong-stock-tooltip"
                  >
                    <template #content>
                      <div class="strong-tooltip">
                        <div class="strong-tooltip-title">{{ theme.theme }} 5%以上</div>
                        <div v-for="stock in theme.ret5_stocks || []" :key="`ret5-${theme.theme}-${stock.code}`" class="strong-tooltip-row">
                          <span>{{ stock.code }} {{ stock.name }}</span>
                          <b :class="toneClass(stock.ret)">{{ fmtPct(stock.ret) }}</b>
                          <em>{{ stock.mode || '观察' }}</em>
                        </div>
                      </div>
                    </template>
                    <span class="strong-count">5%以上 {{ theme.ret5_count }} 只</span>
                  </el-tooltip>
                  ，
                  <el-tooltip
                    placement="top"
                    :disabled="!(theme.ret8_stocks || []).length"
                    popper-class="strong-stock-tooltip"
                  >
                    <template #content>
                      <div class="strong-tooltip">
                        <div class="strong-tooltip-title">{{ theme.theme }} 8%以上</div>
                        <div v-for="stock in theme.ret8_stocks || []" :key="`ret8-${theme.theme}-${stock.code}`" class="strong-tooltip-row">
                          <span>{{ stock.code }} {{ stock.name }}</span>
                          <b :class="toneClass(stock.ret)">{{ fmtPct(stock.ret) }}</b>
                          <em>{{ stock.mode || '观察' }}</em>
                        </div>
                      </div>
                    </template>
                    <span class="strong-count strong-count-hot">8%以上 {{ theme.ret8_count }} 只</span>
                  </el-tooltip>
                  ，前3均涨 {{ fmtPct(theme.top3_avg_ret) }}
                </div>
              </div>
              <div class="theme-modes">
                <el-tag
                  v-for="(count, mode) in theme.mode_counts"
                  :key="mode"
                  size="small"
                  effect="plain"
                >
                  {{ mode }} {{ count }}
                </el-tag>
              </div>
            </header>

            <el-table
              :data="theme.stocks"
              size="small"
              row-key="code"
              :row-class-name="mainlineRowClass"
              @row-click="selectMainlineRow"
            >
              <el-table-column label="股票" min-width="150">
                <template #default="{ row }">
                  <div class="stock-cell">
                    <el-button link type="primary" class="table-code" @click.stop="openIndicators(row)">
                      {{ row.code }}
                    </el-button>
                    <span>{{ row.name }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="模式" min-width="126">
                <template #default="{ row }">
                  <el-tag size="small" :type="modeTone(row.trade_mode)">
                    {{ row.trade_mode || row.signal_type || '观察' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="主线位次" width="92" align="center">
                <template #default="{ row }">#{{ row.sector_rank || '-' }} / {{ row.sector_strong_count || 0 }}</template>
              </el-table-column>
              <el-table-column label="涨幅" width="82" align="right">
                <template #default="{ row }"><span :class="toneClass(row.ret_vs_prevclose)">{{ fmtPct(row.ret_vs_prevclose) }}</span></template>
              </el-table-column>
              <el-table-column label="回撤" width="82" align="right">
                <template #default="{ row }">{{ fmtPct(row.pullback) }}</template>
              </el-table-column>
              <el-table-column label="量能" width="82" align="right">
                <template #default="{ row }">{{ fmtRatio(row.amt_vs_prev) }}</template>
              </el-table-column>
              <el-table-column label="距30高" width="86" align="right">
                <template #default="{ row }">{{ fmtPct(row.distance_to_30d_high) }}</template>
              </el-table-column>
              <el-table-column label="核心分" width="82" align="right">
                <template #default="{ row }">{{ fmtNum(row.score, 1) }}</template>
              </el-table-column>
              <el-table-column label="风险" min-width="160">
                <template #default="{ row }">
                  <span class="risk-text">{{ riskText(row) || '—' }}</span>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="filteredWatchThemeGroups.length" class="watch-pool">
            <header class="watch-head">
              <div>
                <div class="watch-title">观察池</div>
                <div class="watch-sub">
                  09:45 后低位突破 / 修复反包 / 急拉观察只做线索复盘，不进入主推荐；09:45 前口径仍会直接参与主列表。
                </div>
              </div>
              <el-tag size="small" effect="plain">共 {{ watchVisibleCount }} 只</el-tag>
            </header>

            <section
              v-for="theme in filteredWatchThemeGroups"
              :key="`watch-${theme.theme}`"
              class="watch-theme"
            >
              <div class="watch-theme-head">
                <div>
                  <span class="theme-rank">#{{ theme.rank }}</span>
                  <b>{{ theme.theme }}</b>
                  <span class="watch-theme-sub">
                    强度 {{ fmtNum(theme.score, 1) }}，
                    <el-tooltip
                      placement="top"
                      :disabled="!(theme.ret5_stocks || []).length"
                      popper-class="strong-stock-tooltip"
                    >
                      <template #content>
                        <div class="strong-tooltip">
                          <div class="strong-tooltip-title">{{ theme.theme }} 5%以上</div>
                          <div v-for="stock in theme.ret5_stocks || []" :key="`watch-ret5-${theme.theme}-${stock.code}`" class="strong-tooltip-row">
                            <span>{{ stock.code }} {{ stock.name }}</span>
                            <b :class="toneClass(stock.ret)">{{ fmtPct(stock.ret) }}</b>
                            <em>{{ stock.mode || '观察' }}</em>
                          </div>
                        </div>
                      </template>
                      <span class="strong-count">5%以上 {{ theme.ret5_count }} 只</span>
                    </el-tooltip>
                    ，
                    <el-tooltip
                      placement="top"
                      :disabled="!(theme.ret8_stocks || []).length"
                      popper-class="strong-stock-tooltip"
                    >
                      <template #content>
                        <div class="strong-tooltip">
                          <div class="strong-tooltip-title">{{ theme.theme }} 8%以上</div>
                          <div v-for="stock in theme.ret8_stocks || []" :key="`watch-ret8-${theme.theme}-${stock.code}`" class="strong-tooltip-row">
                            <span>{{ stock.code }} {{ stock.name }}</span>
                            <b :class="toneClass(stock.ret)">{{ fmtPct(stock.ret) }}</b>
                            <em>{{ stock.mode || '观察' }}</em>
                          </div>
                        </div>
                      </template>
                      <span class="strong-count strong-count-hot">8%以上 {{ theme.ret8_count }} 只</span>
                    </el-tooltip>
                  </span>
                </div>
                <div class="theme-modes">
                  <el-tag
                    v-for="(count, mode) in theme.mode_counts"
                    :key="mode"
                    size="small"
                    effect="plain"
                  >
                    {{ mode }} {{ count }}
                  </el-tag>
                </div>
              </div>

              <el-table
                :data="theme.stocks"
                size="small"
                row-key="code"
                :row-class-name="mainlineRowClass"
                @row-click="selectMainlineRow"
              >
                <el-table-column label="股票" min-width="150">
                  <template #default="{ row }">
                    <div class="stock-cell">
                      <el-button link type="primary" class="table-code" @click.stop="openIndicators(row)">
                        {{ row.code }}
                      </el-button>
                      <span>{{ row.name }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="模式" min-width="126">
                  <template #default="{ row }">
                    <el-tag size="small" :type="modeTone(row.trade_mode)">
                      {{ row.trade_mode || row.signal_type || '观察' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="主线位次" width="92" align="center">
                  <template #default="{ row }">#{{ row.sector_rank || '-' }} / {{ row.sector_strong_count || 0 }}</template>
                </el-table-column>
                <el-table-column label="涨幅" width="82" align="right">
                  <template #default="{ row }"><span :class="toneClass(row.ret_vs_prevclose)">{{ fmtPct(row.ret_vs_prevclose) }}</span></template>
                </el-table-column>
                <el-table-column label="回撤" width="82" align="right">
                  <template #default="{ row }">{{ fmtPct(row.pullback) }}</template>
                </el-table-column>
                <el-table-column label="量能" width="82" align="right">
                  <template #default="{ row }">{{ fmtRatio(row.amt_vs_prev) }}</template>
                </el-table-column>
                <el-table-column label="距30高" width="86" align="right">
                  <template #default="{ row }">{{ fmtPct(row.distance_to_30d_high) }}</template>
                </el-table-column>
                <el-table-column label="核心分" width="82" align="right">
                  <template #default="{ row }">{{ fmtNum(row.score, 1) }}</template>
                </el-table-column>
                <el-table-column label="风险" min-width="160">
                  <template #default="{ row }">
                    <span class="risk-text">{{ riskText(row) || '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </section>
          </section>
        </template>

        <template v-else>
          <el-empty v-if="!loading && !filteredRows.length" description="暂无龙头强势信号" />

          <StockSignalDetail
            v-for="row in filteredRows"
            :key="row.code"
            :row="row"
            :date="meta.date"
            :active-mode="activeMode"
            editable-theme
            clickable-code
            @open-indicators="openIndicators"
            @load-theme-options="loadDominantOptions"
            @save-theme="saveTradeTheme"
          />
        </template>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import axios from 'axios'
import StockSignalDetail from '@/components/StockSignalDetail.vue'

const router = useRouter()
const loading = ref(false)
const detailLoading = ref(false)
const activeMode = ref('strict')
const rows = ref([])
const themeGroups = ref([])
const watchThemeGroups = ref([])
const mainlineSummary = ref(null)
const selectedDetail = ref(null)
const selectedKey = ref('')
const meta = ref({})
const queryDate = ref('')
const snapshotTime = ref('')
const manualSnapshot = ref(false)
const keyword = ref('')
const maxSectorRank = ref(3)
const minSectorStrong = ref(3)
const minScore = ref(72)
const limit = ref(100)
const mainlineTheme = ref('auto')
const marketFilter = ref([])
const autoRefresh = ref(false)
const globalSectorOptions = ref([])
let timer = null

const filteredRows = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return rows.value.filter(row => {
    if (marketFilter.value.length && !marketFilter.value.some(m => matchMarket(row.code, m))) return false
    if (!kw) return true
    return [row.code, row.name, row.trade_theme, row.best_sector, row.tags]
      .filter(Boolean)
      .some(v => String(v).toLowerCase().includes(kw))
  })
})

function filterThemeGroups(groups) {
  const kw = keyword.value.trim().toLowerCase()
  return groups.map(group => {
    const themeHit = kw && String(group.theme || '').toLowerCase().includes(kw)
    const stocks = (group.stocks || []).filter(row => {
      if (marketFilter.value.length && !marketFilter.value.some(m => matchMarket(row.code, m))) return false
      if (!kw || themeHit) return true
      return [
        row.code,
        row.name,
        row.trade_theme,
        row.best_sector,
        row.mainline_theme,
        row.trade_mode,
        row.tags,
        ...(row.risk_tags || []),
      ].filter(Boolean).some(v => String(v).toLowerCase().includes(kw))
    })
    return { ...group, stocks, candidate_count: stocks.length }
  }).filter(group => group.stocks.length)
}

const filteredThemeGroups = computed(() => (
  activeMode.value === 'mainline' ? filterThemeGroups(themeGroups.value) : []
))
const filteredWatchThemeGroups = computed(() => (
  activeMode.value === 'mainline' ? filterThemeGroups(watchThemeGroups.value) : []
))

const visibleMainlineStocks = computed(() => filteredThemeGroups.value.flatMap(group => group.stocks || []))
const watchVisibleCount = computed(() => filteredWatchThemeGroups.value.reduce((sum, group) => sum + (group.stocks?.length || 0), 0))
const summaryTopBroad = computed(() => (mainlineSummary.value?.top_broad || []).slice(0, 4))

const avgScore = computed(() => {
  const list = activeMode.value === 'mainline' ? visibleMainlineStocks.value : filteredRows.value
  if (!list.length) return '—'
  const v = list.reduce((sum, r) => sum + Number(r.score || 0), 0) / list.length
  return v.toFixed(1)
})

const sectorCount = computed(() => {
  if (activeMode.value === 'mainline') return filteredThemeGroups.value.length || '—'
  return new Set(filteredRows.value.map(r => r.trade_theme || r.best_sector).filter(Boolean)).size || '—'
})
const modeTitle = computed(() => {
  if (activeMode.value !== 'mainline') return '龙头强势'
  return meta.value.list_mode_name ? `主线核心（${meta.value.list_mode_name}）` : '主线核心'
})

function normalizeRow(row) {
  return {
    ...row,
    _dominantDraft: row.trade_theme || row.best_sector || row.mainline_theme || '',
    _sectorOptions: (row.trade_theme || row.best_sector || row.mainline_theme)
      ? [row.trade_theme || row.best_sector || row.mainline_theme]
      : [],
    _sectorLoading: false,
  }
}

async function fetchList(force = false) {
  loading.value = true
  try {
    const params = activeMode.value === 'mainline'
      ? {
          max_sector_rank: maxSectorRank.value,
          min_sector_strong: minSectorStrong.value,
          min_ret: -3,
          max_ret: 35,
          min_amt_ratio: 0.3,
          min_amount: 5000000,
          theme: mainlineTheme.value,
          limit: limit.value,
          include_bars: 0,
        }
      : {
          max_sector_rank: maxSectorRank.value,
          min_sector_strong: minSectorStrong.value,
          min_score: minScore.value,
          limit: limit.value,
        }
    if (queryDate.value) params.date = queryDate.value
    if (snapshotTime.value && manualSnapshot.value) params.snapshot = snapshotTime.value
    if (force) params.refresh = '1'
    const url = activeMode.value === 'mainline' ? '/api/mainline_core' : '/api/leader_strength'
    const res = await axios.get(url, { params, timeout: 90000 })
    rows.value = (res.data.data || []).map(normalizeRow)
    themeGroups.value = (res.data.themes || []).map(group => ({
      ...group,
      stocks: (group.stocks || []).map(normalizeRow),
    }))
    watchThemeGroups.value = activeMode.value === 'mainline'
      ? (res.data.watch_themes || []).map(group => ({
          ...group,
          stocks: (group.stocks || []).map(normalizeRow),
        }))
      : []
    mainlineSummary.value = activeMode.value === 'mainline' ? (res.data.summary || null) : null
    meta.value = {
      date: res.data.date,
      snapshot: res.data.snapshot,
      latest_time: res.data.latest_time,
      list_mode: res.data.list_mode,
      list_mode_name: res.data.list_mode_name,
      watch_count: res.data.watch_count,
      watch_theme_count: res.data.watch_theme_count,
    }
    if (!queryDate.value) queryDate.value = res.data.date || ''
    if (!manualSnapshot.value) snapshotTime.value = res.data.snapshot || ''
    if (activeMode.value === 'mainline') {
      const watchRows = watchThemeGroups.value.flatMap(group => group.stocks || [])
      const first = themeGroups.value[0]?.stocks?.[0] || watchThemeGroups.value[0]?.stocks?.[0]
      const current = rows.value.find(r => r.code === selectedKey.value) || watchRows.find(r => r.code === selectedKey.value)
      if (current || first) {
        await selectMainlineRow(current || first)
      }
    }
  } catch (e) {
    ElMessage.error('龙头强势加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function switchMode(mode) {
  if (mode === 'mainline') {
    maxSectorRank.value = 8
    minSectorStrong.value = 0
    limit.value = 300
  } else {
    maxSectorRank.value = 3
    minSectorStrong.value = 3
    minScore.value = 72
    limit.value = 100
  }
  mainlineSummary.value = null
  watchThemeGroups.value = []
  selectedDetail.value = null
  selectedKey.value = ''
  fetchList(true)
}

function onSnapshotChange() {
  manualSnapshot.value = Boolean(snapshotTime.value)
  fetchList(true)
}

function clearSnapshot() {
  manualSnapshot.value = false
  snapshotTime.value = ''
  fetchList(true)
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  clearInterval(timer)
  timer = null
  if (autoRefresh.value) {
    fetchList(true)
    timer = setInterval(() => fetchList(true), 30000)
  }
}

function openIndicators(row) {
  router.push({ path: '/indicators', query: { code: row.code, name: row.name, date: meta.value.date } })
}

async function loadDominantOptions(row) {
  if (row._sectorLoaded) return
  row._sectorLoading = true
  try {
    await loadGlobalSectorOptions()
    const res = await axios.get('/api/trade_theme/stock', { params: { code: row.code } })
    const opts = new Set()
    for (const s of globalSectorOptions.value) opts.add(s)
    const theme = res.data.theme || res.data.dominant
    if (theme?.sector) opts.add(theme.sector)
    for (const s of res.data.sectors || []) opts.add(s)
    if (row.trade_theme || row.best_sector) opts.add(row.trade_theme || row.best_sector)
    row._sectorOptions = [...opts]
    row._dominantDraft = theme?.sector || row.trade_theme || row.best_sector || ''
    row._sectorLoaded = true
  } catch (e) {
    ElMessage.error('加载候选主线失败：' + (e.response?.data?.error || e.message))
  } finally {
    row._sectorLoading = false
  }
}

async function loadGlobalSectorOptions() {
  if (globalSectorOptions.value.length) return
  const [themeRes, rawRes] = await Promise.all([
    axios.get('/api/trade_theme_list'),
    axios.get('/api/sector_list'),
  ])
  const opts = new Set()
  for (const s of themeRes.data.data || []) opts.add(s.sector)
  for (const s of rawRes.data.data || []) opts.add(s.sector)
  globalSectorOptions.value = [...opts].filter(Boolean).sort()
}

async function saveTradeTheme(row) {
  const sector = String(row._dominantDraft || '').trim()
  if (!sector || sector === (row.trade_theme || row.best_sector)) return
  row._sectorLoading = true
  try {
    await axios.put('/api/trade_theme/stock', { code: row.code, sector })
    ElMessage.success(`${row.code} 交易主线已改为 ${sector}`)
    row.best_sector = sector
    row.trade_theme = sector
    row.mainline_theme = sector
    row._sectorOptions = [...new Set([sector, ...(row._sectorOptions || [])])]
    await fetchList(true)
  } catch (e) {
    ElMessage.error('保存交易主线失败：' + (e.response?.data?.error || e.message))
  } finally {
    row._sectorLoading = false
  }
}

async function selectMainlineRow(row) {
  if (!row?.code) return
  selectedKey.value = row.code
  selectedDetail.value = {
    ...(selectedDetail.value?.code === row.code ? selectedDetail.value : {}),
    ...normalizeRow(row),
  }
  detailLoading.value = true
  try {
    const res = await axios.get('/api/stock_signal_detail', {
      params: { code: row.code, date: meta.value.date, snapshot: meta.value.snapshot },
      timeout: 60000,
    })
    const detail = res.data.data || {}
    selectedDetail.value = normalizeRow({
      ...detail,
      ...row,
      today_bars: detail.today_bars || [],
      prev_bars: detail.prev_bars || [],
      prev_date: detail.prev_date || row.prev_date || row.prev_d,
      score: row.score ?? detail.score,
      core_score: row.core_score ?? detail.core_score,
      trade_mode: row.trade_mode || detail.trade_mode,
      risk_tags: row.risk_tags || detail.risk_tags,
    })
  } catch (e) {
    ElMessage.error('加载股票详情失败：' + (e.response?.data?.error || e.message))
  } finally {
    detailLoading.value = false
  }
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

function fmtRatio(v) {
  if (v === null || v === undefined || v === '') return '-'
  return `${fmtNum(v, 2)}x`
}

function toneClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'text-up' : 'text-down'
}

function modeTone(mode) {
  if (mode === '主线核心追强') return 'danger'
  if (mode === '核心中位承接') return 'warning'
  if (mode === '主线低位突破') return 'success'
  if (mode === '修复反包观察') return 'info'
  if (mode === '09:45后急拉观察') return 'info'
  return 'info'
}

function themeStatusTone(status) {
  if (status === '主线确认') return 'danger'
  if (status === '发酵中') return 'warning'
  if (status === '分歧中') return 'info'
  return 'info'
}

function summaryTone(status) {
  if (status === '主线明确') return 'danger'
  if (status === '主线发酵') return 'warning'
  if (status === '主线分散') return 'info'
  return 'info'
}

function riskText(row) {
  if (Array.isArray(row.risk_tags) && row.risk_tags.length) return row.risk_tags.join(' / ')
  return String(row.tags || '').split(',').filter(tag => /回落|风险|过热|不足|偏高|后排|弱/.test(tag)).slice(0, 3).join(' / ')
}

function mainlineRowClass({ row }) {
  return row.code === selectedKey.value ? 'is-selected-mainline' : ''
}

function matchMarket(code, market) {
  if (market === 'sh') return code.startsWith('6') && !code.startsWith('688') && !code.startsWith('689')
  if (market === 'sz') return code.startsWith('00')
  if (market === 'cyb') return code.startsWith('300') || code.startsWith('301')
  if (market === 'kcb') return code.startsWith('688') || code.startsWith('689')
  if (market === 'bj') return code.startsWith('9')
  return true
}

onMounted(() => fetchList())
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.leader-page {
  height: 100%;
  min-height: calc(100vh - 84px);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid #ebeef5;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.content-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 12px;
}

.filters {
  align-self: start;
  position: sticky;
  top: 0;
  display: grid;
  gap: 10px;
}

.filter-block {
  background: #fff;
  border: 1px solid #ebeef5;
  padding: 12px;
}

.filter-title {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 700;
  color: #303133;
}

.filter-block label {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: #606266;
}

.market-list {
  display: grid;
  gap: 4px;
}

.market-list :deep(.el-checkbox) {
  height: 24px;
}

.stats div {
  display: flex;
  justify-content: space-between;
  padding: 5px 0;
  font-size: 12px;
  color: #606266;
}

.stats b {
  color: #303133;
}

.result-list {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.market-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid #ebeef5;
}

.summary-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #303133;
  font-size: 13px;
  line-height: 1.5;
}

.summary-main span:last-child {
  min-width: 0;
  word-break: break-word;
}

.summary-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  flex-shrink: 0;
}

.theme-section {
  background: #fff;
  border: 1px solid #ebeef5;
}

.watch-pool {
  background: #fff;
  border: 1px solid #e4e7ed;
}

.watch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border-bottom: 1px solid #ebeef5;
  background: #fafafa;
}

.watch-title {
  font-size: 15px;
  font-weight: 700;
  color: #303133;
}

.watch-sub,
.watch-theme-sub {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.watch-theme {
  border-top: 1px solid #f2f3f5;
}

.watch-theme:first-of-type {
  border-top: 0;
}

.watch-theme-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  color: #303133;
}

.theme-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border-bottom: 1px solid #ebeef5;
}

.theme-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  color: #303133;
}

.theme-rank {
  color: #409eff;
  font-weight: 700;
}

.theme-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.strong-count {
  color: #409eff;
  cursor: help;
}

.strong-count-hot {
  color: #f56c6c;
}

:global(.strong-stock-tooltip) {
  max-width: 440px;
}

:global(.strong-tooltip) {
  display: grid;
  gap: 4px;
  min-width: 280px;
  max-height: 360px;
  overflow: auto;
}

:global(.strong-tooltip-title) {
  padding-bottom: 4px;
  font-weight: 700;
  color: #fff;
}

:global(.strong-tooltip-row) {
  display: grid;
  grid-template-columns: minmax(130px, 1fr) 58px minmax(72px, auto);
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
  white-space: nowrap;
}

:global(.strong-tooltip-row b) {
  text-align: right;
  font-weight: 700;
}

:global(.strong-tooltip-row em) {
  color: #c0c4cc;
  font-style: normal;
}

.theme-modes {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: wrap;
}

.stock-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.table-code {
  padding: 0;
  font-weight: 700;
}

.risk-text {
  color: #909399;
  font-size: 12px;
}

.text-up {
  color: #f56c6c;
}

.text-down {
  color: #67c23a;
}

:deep(.is-selected-mainline td) {
  background: #ecf5ff !important;
}

@media (max-width: 1100px) {
  .content-grid {
    grid-template-columns: 1fr;
  }

  .filters {
    position: static;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .theme-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .market-summary {
    align-items: flex-start;
    flex-direction: column;
  }

}
</style>
