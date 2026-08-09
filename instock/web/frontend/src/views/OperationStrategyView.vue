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

    <!-- ── 今日推荐 Top 5 ── -->
    <div class="daily-recommend-band" v-if="dailyRecommend.length">
      <div class="dr-header">
        <strong>📌 今日推荐</strong>
        <el-tag size="small" :type="marketGateType" effect="dark">{{ marketEnv.status }}</el-tag>
        <span class="dr-snapshot">{{ meta.snapshot || recommendMeta.snapshot || '-' }}</span>
      </div>
      <div class="dr-cards">
        <div v-for="(rec, idx) in dailyRecommend" :key="rec.code" class="dr-card" :class="'dr-rank-' + (idx+1)">
          <div class="dr-rank-badge">#{{ idx + 1 }}</div>
          <div class="dr-code-name">
            <button class="stock-link" @click="openStockCharts(rec)">{{ rec.code }}</button>
            <span class="dr-name">{{ rec.name }}</span>
          </div>
          <div class="dr-theme">{{ rec.trade_theme || '-' }}
            <el-tag size="small" :type="rec.sector_rank <= 3 ? 'danger' : 'info'">#{{ rec.sector_rank }}</el-tag>
          </div>
          <div class="dr-metrics">
            <div class="dr-metric">
              <span class="dr-m-label">推荐分</span>
              <span class="dr-m-val dr-score">{{ rec.recommend_score }}</span>
            </div>
            <div class="dr-metric">
              <span class="dr-m-label">涨幅</span>
              <span class="dr-m-val" :class="rec.current_change > 0 ? 'text-up' : 'text-down'">{{ fmtPct(rec.current_change) }}</span>
            </div>
            <div class="dr-metric">
              <span class="dr-m-label">量比</span>
              <span class="dr-m-val">{{ rec.amt_vs_prev }}x</span>
            </div>
            <div class="dr-metric">
              <span class="dr-m-label">买力</span>
              <span class="dr-m-val">{{ fmtPct(rec.buy_strength) }} {{ rec.buy_trend }}</span>
            </div>
            <div class="dr-metric">
              <span class="dr-m-label">距30日高</span>
              <span class="dr-m-val">{{ rec.distance_30d }}%</span>
            </div>
            <div class="dr-metric">
              <span class="dr-m-label">持续性</span>
              <el-tag size="small" :type="rec.continuity_check ? 'success' : 'warning'" effect="plain">
                {{ rec.continuity_check ? '✓' : '⚠' }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="daily-recommend-band dr-empty" v-else-if="recommendReason">
      <div class="dr-header">
        <strong>📌 今日推荐</strong>
        <span class="dr-snapshot">{{ recommendReason }}</span>
      </div>
    </div>

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

    <section class="burst-candidates-band">
      <div class="burst-candidates-head">
        <div>
          <strong>日内爆发候选</strong>
          <span>参照买点 {{ intradayBurst.buy_reference_time || '-' }}</span>
        </div>
        <el-tag size="small" type="warning" effect="plain">{{ intradayBurst.version || 'V2' }}</el-tag>
      </div>
      <div v-if="intradayBurst.data?.length" class="burst-candidates-list">
        <div v-for="(row, index) in intradayBurst.data" :key="row.code" class="burst-candidate-item">
          <span class="burst-rank">{{ index + 1 }}</span>
          <div class="burst-stock">
            <button class="burst-stock-name" type="button" @click="openStockCharts(row)">{{ row.code }} {{ row.name }}</button>
            <span>{{ row.mainline_theme || row.trade_theme || '-' }}</span>
          </div>
          <el-tag size="small" type="danger">爆发分 {{ fmtNum(row.intraday_burst_score, 1) }}</el-tag>
          <el-tag v-if="row.limitup_gene_label" size="small" type="warning" effect="plain">{{ row.limitup_gene_label }}</el-tag>
          <span :class="toneClass(row.ret_vs_prevclose)">{{ fmtPct(row.ret_vs_prevclose) }}</span>
          <div class="burst-factor-list">
            <span v-for="factor in row.intraday_burst_factors || []" :key="factor">{{ factor }}</span>
            <span v-for="penalty in row.intraday_burst_penalties || []" :key="penalty" class="burst-penalty">{{ penalty }}</span>
          </div>
          <el-button size="small" :icon="EditPen" @click="recordStock(row)">记录</el-button>
        </div>
      </div>
      <div v-else class="burst-empty">当前市场环境未生成日内爆发候选</div>
    </section>

    <section class="burst-candidates-band burst-candidates-v3">
      <div class="burst-candidates-head">
        <div>
          <strong>日内爆发候选</strong>
          <span>涨幅低于 9%，参照买点 {{ intradayBurstV3.buy_reference_time || '-' }}</span>
        </div>
        <el-tag size="small" type="success" effect="plain">{{ intradayBurstV3.version || 'V3' }}</el-tag>
      </div>
      <div v-if="intradayBurstV3.data?.length" class="burst-candidates-list">
        <div v-for="(row, index) in intradayBurstV3.data" :key="row.code" class="burst-candidate-item">
          <span class="burst-rank">{{ index + 1 }}</span>
          <div class="burst-stock">
            <button class="burst-stock-name" type="button" @click="openStockCharts(row)">{{ row.code }} {{ row.name }}</button>
            <span>{{ row.mainline_theme || row.trade_theme || '-' }}</span>
          </div>
          <el-tag size="small" type="danger">爆发分 {{ fmtNum(row.intraday_burst_score, 1) }}</el-tag>
          <el-tag v-if="row.limitup_gene_label" size="small" type="warning" effect="plain">{{ row.limitup_gene_label }}</el-tag>
          <span :class="toneClass(row.ret_vs_prevclose)">{{ fmtPct(row.ret_vs_prevclose) }}</span>
          <div class="burst-factor-list">
            <span v-for="factor in row.intraday_burst_factors || []" :key="factor">{{ factor }}</span>
            <span v-for="penalty in row.intraday_burst_penalties || []" :key="penalty" class="burst-penalty">{{ penalty }}</span>
          </div>
          <el-button size="small" :icon="EditPen" @click="recordStock(row)">记录</el-button>
        </div>
      </div>
      <div v-else class="burst-empty">当前没有涨幅低于 9% 的日内爆发候选</div>
    </section>

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
          <div class="card-actions">
            <el-badge :value="activeStockFilterCount" :hidden="!activeStockFilterCount" class="stock-filter-badge">
              <el-button size="small" :icon="Filter" @click="stockFilterOpen = true">筛选</el-button>
            </el-badge>
            <el-tooltip content="调整个股列顺序" placement="top">
              <el-button circle size="small" :icon="Setting" @click="columnOrderOpen = true" />
            </el-tooltip>
            <el-radio-group v-model="viewMode" size="small">
              <el-radio-button value="themes">按主线</el-radio-button>
              <el-radio-button value="stocks">按个股</el-radio-button>
            </el-radio-group>
          </div>
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
          :key="`stock-columns:${stockColumnOrder.join('|')}`"
          border
          size="small"
          row-key="code"
          class="main-table"
        >
          <el-table-column prop="code" label="代码" width="86" fixed sortable>
            <template #default="{ row }">
              <button class="stock-chart-trigger" type="button" @click="openStockCharts(row)">{{ row.code }}</button>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" width="110" fixed sortable>
            <template #default="{ row }">
              <button class="stock-chart-trigger" type="button" @click="openStockCharts(row)">{{ row.name || '-' }}</button>
            </template>
          </el-table-column>
          <el-table-column
            v-for="column in stockColumns"
            :key="column.key"
            :prop="column.prop"
            :label="column.label"
            :width="column.width"
            :min-width="column.minWidth"
            :align="column.align"
            sortable
            :sort-method="column.key === 'risk' ? sortRisk : undefined"
          >
            <template #default="{ row }">
              <template v-if="column.key === 'recommend_count'">
                <el-tooltip :content="recommendTip(row)" placement="top">
                  <span class="recommend-count" :class="recommendCountClass(row.recommend_count)">{{ row.recommend_count ?? 0 }}</span>
                </el-tooltip>
              </template>
              <template v-else-if="column.key === 'ret_acceleration'">
                <el-tooltip :content="burstTip(row)" placement="top">
                  <el-tag v-if="row.burst_label" size="small" type="danger">{{ row.burst_label }}</el-tag>
                  <span v-else>{{ fmtPct(row.ret_acceleration) }}</span>
                </el-tooltip>
              </template>
              <template v-else-if="column.key === 'persistence'">
                <el-tooltip :content="persistenceTip(row)" placement="top">
                  <el-tag size="small" :type="persistenceType(row.acceleration_persistence_label)">{{ row.acceleration_persistence_label || '-' }}</el-tag>
                </el-tooltip>
              </template>
              <template v-else-if="column.key === 'push_efficiency'">
                <el-tooltip :content="pushEfficiencyTip(row)" placement="top">
                  <span :class="pushEfficiencyClass(row.push_efficiency)">{{ fmtNum(row.push_efficiency, 2) }}</span>
                </el-tooltip>
              </template>
              <template v-else-if="column.key === 'mainline_theme'">{{ row.mainline_theme || row.trade_theme || row.best_sector || '-' }}</template>
              <template v-else-if="column.key === 'trade_mode'"><el-tag size="small" :type="modeType(row.trade_mode)">{{ row.trade_mode || '-' }}</el-tag></template>
              <template v-else-if="column.key === 'first_candidate_ret'">
                <el-tooltip :content="firstCandidateRetTip(row)" placement="top">
                  <span :class="toneClass(row.first_candidate_ret_vs_prevclose)">{{ fmtPct(row.first_candidate_ret_vs_prevclose) }}</span>
                </el-tooltip>
              </template>
              <template v-else-if="column.key === 'current_change_pct'"><span :class="toneClass(row.current_change_pct)">{{ fmtPct(row.current_change_pct) }}</span></template>
              <template v-else-if="column.key === 'amt_vs_prev'">{{ fmtNum(row.amt_vs_prev, 2) }}</template>
              <template v-else-if="column.key === 'volume_follow_through'">
                <el-tooltip :content="volumeTip(row)" placement="top"><el-tag size="small" :type="volumeType(row.volume_follow_through_label)">{{ row.volume_follow_through_label || '-' }}</el-tag></el-tooltip>
              </template>
              <template v-else-if="column.key === 'execution_label'"><el-tag size="small" :type="executionType(row.execution_label)">{{ row.execution_label || '-' }}</el-tag></template>
              <template v-else-if="column.key === 'prob_label'">
                <el-tag v-if="row.prob_label" :type="probTagType(row.prob_color)" size="small" :title="row.prob_tip">{{ row.prob_icon }} {{ probLabelText(row.prob_label) }}</el-tag>
                <span v-else>-</span>
              </template>
              <template v-else-if="column.key === 'risk'"><span class="risk-text">{{ riskText(row) || '-' }}</span></template>
              <template v-else>{{ row[column.prop] ?? '-' }}</template>
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

    <el-drawer v-model="columnOrderOpen" title="个股列顺序" direction="rtl" size="320px">
      <div class="column-order-panel">
        <div
          v-for="column in stockColumns"
          :key="column.key"
          class="column-order-item"
          draggable="true"
          @dragstart="onColumnDragStart(column.key, $event)"
          @dragover.prevent
          @drop="onColumnDrop(column.key, $event)"
        >
          <el-icon class="column-drag-handle"><Rank /></el-icon>
          <span>{{ column.label }}</span>
          <div class="column-order-actions">
            <el-tooltip content="前移" placement="top">
              <el-button text size="small" :icon="ArrowUp" :disabled="!canMoveColumn(column.key, -1)" @click="moveColumn(column.key, -1)" />
            </el-tooltip>
            <el-tooltip content="后移" placement="top">
              <el-button text size="small" :icon="ArrowDown" :disabled="!canMoveColumn(column.key, 1)" @click="moveColumn(column.key, 1)" />
            </el-tooltip>
          </div>
        </div>
      </div>
    </el-drawer>

    <el-drawer v-model="stockFilterOpen" title="个股筛选" direction="rtl" size="360px">
      <div class="stock-filter-panel">
        <div class="stock-filter-summary">显示 {{ stockRows.length }} / {{ rows.length }} 只</div>
        <el-form label-position="top" class="stock-filter-form">
          <el-form-item label="代码">
            <el-input v-model="stockFilters.code" clearable placeholder="代码关键词" />
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="stockFilters.name" clearable placeholder="名称关键词" />
          </el-form-item>
          <el-form-item label="主线">
            <el-select v-model="stockFilters.mainline" multiple filterable clearable collapse-tags placeholder="全部主线">
              <el-option v-for="value in stockFilterOptions.mainline" :key="value" :label="value" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="模式">
            <el-select v-model="stockFilters.mode" multiple clearable collapse-tags placeholder="全部模式">
              <el-option v-for="value in stockFilterOptions.mode" :key="value" :label="value" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="量能状态">
            <el-select v-model="stockFilters.volume" multiple clearable collapse-tags placeholder="全部量能状态">
              <el-option v-for="value in stockFilterOptions.volume" :key="value" :label="value" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="观察标签">
            <el-select v-model="stockFilters.execution" multiple clearable collapse-tags placeholder="全部观察标签">
              <el-option v-for="value in stockFilterOptions.execution" :key="value" :label="value" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="胜率">
            <el-select v-model="stockFilters.probability" multiple clearable collapse-tags placeholder="全部胜率">
              <el-option v-for="value in stockFilterOptions.probability" :key="value" :label="probLabelText(value)" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="风险">
            <el-input v-model="stockFilters.risk" clearable placeholder="风险关键词" />
          </el-form-item>
        </el-form>

        <div class="stock-filter-section-title">数值范围</div>
        <div v-for="field in numericFilterFields" :key="field.key" class="number-filter-row">
          <span>{{ field.label }}</span>
          <el-input-number v-model="stockFilters.range[field.key].min" :controls="false" :precision="2" placeholder="最小" />
          <span class="number-filter-divider">至</span>
          <el-input-number v-model="stockFilters.range[field.key].max" :controls="false" :precision="2" placeholder="最大" />
        </div>
        <div class="stock-filter-actions">
          <el-button @click="clearStockFilters">清空筛选</el-button>
          <el-button type="primary" @click="stockFilterOpen = false">完成</el-button>
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="stockChartsOpen" :title="`${stockCharts.row?.name || stockCharts.row?.code || ''} 信号详情`" width="90%" top="3vh" destroy-on-close>
      <div v-loading="stockChartsLoading" class="stock-signal-dialog">
        <StockSignalDetail
          v-if="stockCharts.row?.code"
          :row="stockCharts.row"
          :date="stockCharts.date"
          active-mode="mainline"
          volume-label="量比"
          :metric-fields="['volume', 'efficiency', 'day_close']"
          :daily-bars="stockCharts.dailyBars"
          :focus-time="stockCharts.focusTime || ''"
        />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp, EditPen, Filter, Rank, Refresh, Setting } from '@element-plus/icons-vue'
import axios from 'axios'
import StockSignalDetail from '@/components/StockSignalDetail.vue'

const router = useRouter()
const loading = ref(false)
const viewMode = ref('themes')
const query = ref({
  date: '',
  snapshot: '09:45',
  markets: [],
  highProbOnly: false,  // 新增：只看高概率
})
const meta = ref({})
const summary = ref({})
const marketEnv = ref({})
const intradayBurst = ref({})
const intradayBurstV3 = ref({})
const rawThemeGroups = ref([])
const rows = ref([])
const dailyRecommend = ref([])
const recommendReason = ref('')
const recommendMeta = ref({})
const columnOrderOpen = ref(false)
const stockFilterOpen = ref(false)
const klineCache = new Map()
const dailyKlineCache = new Map()
const stockChartsOpen = ref(false)
const stockChartsLoading = ref(false)
const stockCharts = ref({ date: '', row: null, dailyBars: [] })
const STOCK_COLUMN_STORAGE_KEY = 'instock:operation-strategy:stock-columns:v1'
const DEFAULT_STOCK_COLUMNS = [
  { key: 'recommend_count', prop: 'recommend_count', label: '次数', width: 72, align: 'center' },
  { key: 'ret_acceleration', prop: 'ret_acceleration', label: '爆发', width: 104, align: 'center' },
  { key: 'persistence', prop: 'acceleration_persistence_rate', label: '持续性', width: 102, align: 'center' },
  { key: 'push_efficiency', prop: 'push_efficiency', label: '推进效率', width: 96, align: 'right' },
  { key: 'mainline_theme', prop: 'mainline_theme', label: '主线', minWidth: 140 },
  { key: 'trade_mode', prop: 'trade_mode', label: '模式', width: 126 },
  { key: 'first_candidate_ret', prop: 'first_candidate_ret_vs_prevclose', label: '首次入选涨幅', width: 116, align: 'right' },
  { key: 'current_change_pct', prop: 'current_change_pct', label: '实时涨跌幅', width: 112, align: 'right' },
  { key: 'amt_vs_prev', prop: 'amt_vs_prev', label: '量比', width: 76, align: 'right' },
  { key: 'volume_follow_through', prop: 'volume_follow_through_label', label: '量能状态', width: 104, align: 'center' },
  { key: 'execution_label', prop: 'execution_label', label: '观察标签', width: 104, align: 'center' },
  { key: 'prob_label', prop: 'prob_label', label: '胜率', width: 100, align: 'center' },
  { key: 'risk', prop: '', label: '风险', minWidth: 180 },
]
const numericFilterFields = [
  { key: 'recommend_count', label: '次数', value: row => row.recommend_count },
  { key: 'ret_acceleration', label: '爆发', value: row => row.ret_acceleration },
  { key: 'acceleration_persistence_rate', label: '持续性', value: row => row.acceleration_persistence_rate },
  { key: 'push_efficiency', label: '推进效率', value: row => row.push_efficiency },
  { key: 'first_candidate_ret_vs_prevclose', label: '首次入选涨幅', value: row => row.first_candidate_ret_vs_prevclose },
  { key: 'current_change_pct', label: '实时涨跌幅', value: row => row.current_change_pct },
  { key: 'amt_vs_prev', label: '量比', value: row => row.amt_vs_prev },
]

function createStockFilters() {
  return {
    code: '',
    name: '',
    mainline: [],
    mode: [],
    volume: [],
    execution: [],
    probability: [],
    risk: '',
    range: Object.fromEntries(numericFilterFields.map(field => [field.key, { min: undefined, max: undefined }])),
  }
}

const stockFilters = ref(createStockFilters())

function loadStockColumnOrder() {
  try {
    const saved = JSON.parse(localStorage.getItem(STOCK_COLUMN_STORAGE_KEY) || '[]')
    const valid = Array.isArray(saved)
      ? saved.filter(key => DEFAULT_STOCK_COLUMNS.some(column => column.key === key))
      : []
    for (const column of DEFAULT_STOCK_COLUMNS) {
      if (valid.includes(column.key)) continue
      if (column.key === 'push_efficiency') {
        const persistenceIndex = valid.indexOf('persistence')
        valid.splice(persistenceIndex >= 0 ? persistenceIndex + 1 : valid.length, 0, column.key)
      } else {
        valid.push(column.key)
      }
    }
    return valid
  } catch {
    return DEFAULT_STOCK_COLUMNS.map(column => column.key)
  }
}

const stockColumnOrder = ref(loadStockColumnOrder())
const draggingColumnKey = ref('')
const stockColumns = computed(() => stockColumnOrder.value
  .map(key => DEFAULT_STOCK_COLUMNS.find(column => column.key === key))
  .filter(Boolean)
)

watch(stockColumnOrder, value => {
  try {
    localStorage.setItem(STOCK_COLUMN_STORAGE_KEY, JSON.stringify(value))
  } catch {
    // 浏览器禁用本地存储时仅保留当前会话的排序。
  }
}, { deep: true })

const summaryText = computed(() => summary.value?.sentence || `${meta.value.date || ''} ${meta.value.snapshot || ''}`)
const envAll = computed(() => marketEnv.value?.all || {})
const envPool = computed(() => marketEnv.value?.pool || {})
const envPullback = computed(() => marketEnv.value?.pool_pullback || {})
const marketGateType = computed(() => marketEnv.value?.severity || 'info')
const marketGateClass = computed(() => `gate-${marketEnv.value?.severity || 'info'}`)
const themeGroups = computed(() => rawThemeGroups.value
  .map(group => {
    const stocks = group.stocks || []
    return { ...group, stocks, candidate_count: stocks.length }
  })
  .filter(group => group.stocks.length)
)
const candidateCount = computed(() => themeGroups.value.reduce((sum, group) => sum + Number(group.candidate_count || 0), 0))

function mainlineText(row) {
  return row.mainline_theme || row.trade_theme || row.best_sector || ''
}

function uniqueFilterValues(getValue) {
  return [...new Set(rows.value.map(getValue).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'zh-Hans-CN'))
}

const stockFilterOptions = computed(() => ({
  mainline: uniqueFilterValues(mainlineText),
  mode: uniqueFilterValues(row => row.trade_mode),
  volume: uniqueFilterValues(row => row.volume_follow_through_label),
  execution: uniqueFilterValues(row => row.execution_label),
  probability: uniqueFilterValues(row => row.prob_label),
}))

function textMatches(value, keyword) {
  return !keyword || String(value || '').toLowerCase().includes(String(keyword).trim().toLowerCase())
}

function selectionMatches(value, selections) {
  return !selections?.length || selections.includes(value)
}

function hasRangeValue(value) {
  return value !== undefined && value !== null && value !== ''
}

function rangeMatches(value, range) {
  const number = Number(value)
  if (!hasRangeValue(range?.min) && !hasRangeValue(range?.max)) return true
  if (!Number.isFinite(number)) return false
  if (hasRangeValue(range.min) && number < Number(range.min)) return false
  if (hasRangeValue(range.max) && number > Number(range.max)) return false
  return true
}

const stockRows = computed(() => rows.value.filter(row => {
  const filters = stockFilters.value
  if (!textMatches(row.code, filters.code) || !textMatches(row.name, filters.name)) return false
  if (!selectionMatches(mainlineText(row), filters.mainline)) return false
  if (!selectionMatches(row.trade_mode, filters.mode)) return false
  if (!selectionMatches(row.volume_follow_through_label, filters.volume)) return false
  if (!selectionMatches(row.execution_label, filters.execution)) return false
  if (!selectionMatches(row.prob_label, filters.probability)) return false
  if (!textMatches(riskText(row), filters.risk)) return false
  return numericFilterFields.every(field => rangeMatches(field.value(row), filters.range[field.key]))
}))

const activeStockFilterCount = computed(() => {
  const filters = stockFilters.value
  let count = ['code', 'name', 'risk'].filter(key => String(filters[key] || '').trim()).length
  count += ['mainline', 'mode', 'volume', 'execution', 'probability'].filter(key => filters[key]?.length).length
  count += numericFilterFields.reduce((total, field) => {
    const range = filters.range[field.key]
    return total + Number(hasRangeValue(range?.min) || hasRangeValue(range?.max))
  }, 0)
  return count
})

const marketOptions = [
  { label: '创业板', value: 'cyb' },
  { label: '科创板', value: 'kcb' },
  { label: '京市A股', value: 'bj' },
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
    intradayBurst.value = res.data.intraday_burst || {}
    intradayBurstV3.value = res.data.intraday_burst_v3 || {}
    rawThemeGroups.value = res.data.themes || []
    rows.value = res.data.data || []
    meta.value = {
      date: res.data.date,
      snapshot: res.data.snapshot,
      latest_time: res.data.latest_time,
    }
    if (!query.value.date) query.value.date = res.data.date || ''
    if (!query.value.snapshot) query.value.snapshot = res.data.snapshot || ''
    await loadRecommend(force)
  } catch (e) {
    ElMessage.error('主线数据加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

async function loadRecommend(force = false) {
  try {
    const params = {
      market: query.value.markets?.length ? query.value.markets.join(',') : 'all',
    }
    if (query.value.date) params.date = query.value.date
    if (query.value.snapshot) params.snapshot = query.value.snapshot
    const res = await axios.get('/api/daily_recommend', { params, timeout: 30000 })
    dailyRecommend.value = res.data.recommendations || []
    recommendMeta.value = { date: res.data.date, snapshot: res.data.snapshot }
    recommendReason.value = res.data.reason || (dailyRecommend.value.length === 0 ? '暂无符合条件推荐' : '')
  } catch (e) {
    console.warn('今日推荐加载失败', e)
    dailyRecommend.value = []
    recommendReason.value = '推荐加载失败'
  }
}

async function openStockCharts(row) {
  const date = meta.value.date || query.value.date
  if (!date || !row?.code) return
  const code = String(row.code).padStart(6, '0')
  const previousDate = row.prev_date || row.prev_d || ''
  const todayKey = `${date}:${code}`
  const previousKey = previousDate ? `${previousDate}:${code}` : ''
  const dailyKey = `${date}:${code}`
  const buildDetailRow = (todayBars = [], previousBars = []) => {
    const tags = [
      row.tags,
      row.trade_mode,
      row.intraday_burst_label,
      row.burst_label,
      row.acceleration_persistence_label,
      row.volume_follow_through_label,
      row.execution_label,
      row.limitup_gene_label,
      Array.isArray(row.risk_tags) ? row.risk_tags.join(',') : row.risk_tags,
    ].filter(Boolean).join(',')
    return {
      ...row,
      code,
      name: row.name || code,
      score: row.score ?? row.core_score,
      prev_date: previousDate,
      tags,
      today_bars: todayBars,
      prev_bars: previousBars,
      day_close_return_pct: (() => {
        const last = todayBars.length ? todayBars[todayBars.length - 1] : null
        const pc    = row.prev_close || (todayBars[0]?.pre_close)
        if (!last || !pc || last.time < '14:59') return null
        return ((last.close / pc) - 1) * 100
      })(),
    }
  }
  stockCharts.value = {
    date,
    row: buildDetailRow(klineCache.get(todayKey) || [], klineCache.get(previousKey) || []),
    dailyBars: dailyKlineCache.get(dailyKey) || [],
    focusTime: meta.value?.snapshot || query.value?.snapshot || '',
  }
  stockChartsOpen.value = true
  if (klineCache.has(todayKey) && (!previousKey || klineCache.has(previousKey)) && dailyKlineCache.has(dailyKey)) return

  stockChartsLoading.value = true
  try {
    const [todayRes, previousRes, dailyRes] = await Promise.all([
      klineCache.has(todayKey)
        ? Promise.resolve({ data: { bars: klineCache.get(todayKey) } })
        : axios.get('/api/minute_bars', { params: { code, date }, timeout: 30000 }),
      !previousKey || klineCache.has(previousKey)
        ? Promise.resolve({ data: { bars: previousKey ? klineCache.get(previousKey) : [] } })
        : axios.get('/api/minute_bars', { params: { code, date: previousDate }, timeout: 30000 }),
      dailyKlineCache.has(dailyKey)
        ? Promise.resolve({ data: dailyKlineCache.get(dailyKey) })
        : axios.get('/instock/api_data/kline', { params: { code, date, period: 'daily' }, timeout: 30000 }),
    ])
    const todayBars = todayRes.data?.bars || []
    const previousBars = previousRes.data?.bars || []
    const dailyBars = Array.isArray(dailyRes.data) ? dailyRes.data : []
    klineCache.set(todayKey, todayBars)
    if (previousKey) klineCache.set(previousKey, previousBars)
    dailyKlineCache.set(dailyKey, dailyBars)
    if (stockCharts.value.row?.code === code && stockCharts.value.date === date) {
      stockCharts.value = { date, row: buildDetailRow(todayBars, previousBars), dailyBars,
        focusTime: meta.value?.snapshot || query.value?.snapshot || '' }
    }
  } catch (e) {
    ElMessage.error('行情图加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    stockChartsLoading.value = false
  }
}

function recordStock(row, theme = '') {
  const signalRisk = riskText(row)
  const signalAmountRatio = row.amt_vs_prev ?? ''
  const systemJudgment = [
    `模式：${row.trade_mode || '-'}`,
    `爆发：${row.burst_label || '无'}（涨幅加速${formatSignalNumber(row.ret_acceleration, 2)}%）`,
    `持续性：${row.acceleration_persistence_label || '-'}（${formatSignalNumber(row.acceleration_persistence_rate, 0)}%）`,
    `量能状态：${row.volume_follow_through_label || '-'}`,
    `观察标签：${row.execution_label || '-'}`,
    `日内爆发分：${formatSignalNumber(row.intraday_burst_score, 1)}`,
    `涨停基因：${row.limitup_gene_label || '未命中'}`,
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
      signal_mode: row.trade_mode || '',
      signal_amount_ratio: signalAmountRatio,
      signal_risk: signalRisk,
    },
  })
}

function canMoveColumn(key, direction) {
  const index = stockColumnOrder.value.indexOf(key)
  const target = index + direction
  return index >= 0 && target >= 0 && target < stockColumnOrder.value.length
}

function moveColumn(key, direction) {
  const from = stockColumnOrder.value.indexOf(key)
  const to = from + direction
  if (from < 0 || to < 0 || to >= stockColumnOrder.value.length) return
  const next = [...stockColumnOrder.value]
  next.splice(from, 1)
  next.splice(to, 0, key)
  stockColumnOrder.value = next
}

function clearStockFilters() {
  stockFilters.value = createStockFilters()
}

function onColumnDragStart(key, event) {
  draggingColumnKey.value = key
  event.dataTransfer?.setData('text/plain', key)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onColumnDrop(targetKey, event) {
  const sourceKey = event.dataTransfer?.getData('text/plain') || draggingColumnKey.value
  draggingColumnKey.value = ''
  if (!sourceKey || sourceKey === targetKey) return
  const next = [...stockColumnOrder.value]
  const from = next.indexOf(sourceKey)
  const to = next.indexOf(targetKey)
  if (from < 0 || to < 0) return
  next.splice(from, 1)
  next.splice(to, 0, sourceKey)
  stockColumnOrder.value = next
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

function signedPct(v) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
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

function riskText(row) {
  if (Array.isArray(row.risk_tags) && row.risk_tags.length) return row.risk_tags.join(' / ')
  return String(row.tags || '').split(',').filter(Boolean).slice(0, 3).join(' / ')
}

function recommendTip(row) {
  const windowText = row.recommend_window || '09:35-当前快照'
  const count = row.recommend_count ?? 0
  const streak = row.max_consecutive_count ?? 0
  const snaps = row.recommend_snapshots || '-'
  return `${windowText} 进入候选 ${count} 次；最长连续 ${streak} 分钟；${snaps}`
}

function recommendCountClass(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  if (n >= 10) return 'count-strong'
  if (n >= 4) return 'count-mid'
  return 'count-low'
}

function burstTip(row) {
  const ret = formatSignalNumber(row.ret_acceleration, 2)
  const amount = formatSignalNumber(row.amount_acceleration, 2)
  return `首次入池至当前：涨幅变化 ${ret}%；量比变化 ${amount}`
}

function firstCandidateRetTip(row) {
  const snapshot = row.first_candidate_snapshot || '无早盘候选留痕'
  return `股票首次进入候选池时（${snapshot}）相对昨收的涨跌幅。`
}

function persistenceTip(row) {
  const rate = formatSignalNumber(row.acceleration_persistence_rate, 0)
  return `候选窗口内涨幅向上的分钟占比 ${rate}%；用于区分持续上行与单点急拉。`
}

function pushEfficiencyTip(row) {
  return '相对昨收涨幅 ÷（截至所选时点的累计成交额 / 1亿元）。数值越高，表示单位成交额推动的涨幅越高。'
}

function pushEfficiencyClass(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  if (n >= 3) return 'text-up'
  if (n < 1) return 'text-down'
  return ''
}

function volumeTip(row) {
  const amountAcceleration = formatSignalNumber(row.amount_acceleration, 2)
  return `结合当前量比和候选窗口内量比变化（${amountAcceleration}）判断放量后的承接情况。`
}

function persistenceType(label) {
  if (label === '持续加速') return 'danger'
  if (label === '后段加速') return 'warning'
  if (label === '断续加速') return 'info'
  return 'info'
}

function volumeType(label) {
  if (label === '放量承接') return 'success'
  if (label === '量能跟随') return 'warning'
  if (label === '量能不足') return 'danger'
  return 'info'
}

function executionType(label) {
  if (label === '爆发核心') return 'danger'
  if (label === '加速主线') return 'warning'
  if (label === '强于主线') return 'success'
  return 'info'
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
.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
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
.burst-candidates-band {
  margin-bottom: 12px;
  padding: 10px 14px;
  border: 1px solid #f1d8a7;
  border-radius: 6px;
  background: #fffdf7;
}
.burst-candidates-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.burst-candidates-head > div { display: flex; align-items: baseline; gap: 8px; }
.burst-candidates-head strong { color: #303133; font-size: 14px; }
.burst-candidates-head span { color: #909399; font-size: 12px; }
.burst-candidates-list { display: grid; gap: 6px; }
.burst-candidate-item {
  display: grid;
  grid-template-columns: 24px minmax(150px, 1.25fr) auto auto 72px minmax(200px, 1.5fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 4px 6px;
  border-top: 1px solid #f3ead6;
}
.burst-candidate-item:first-child { border-top: 0; }
.burst-rank { color: #b7791f; font-weight: 700; text-align: center; }
.burst-stock { display: flex; align-items: baseline; gap: 7px; min-width: 0; }
.burst-stock-name {
  overflow: hidden;
  padding: 0;
  border: 0;
  background: transparent;
  color: #303133;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.burst-stock-name:hover { color: #409eff; }
.burst-stock span { overflow: hidden; color: #909399; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.burst-factor-list { display: flex; flex-wrap: wrap; gap: 5px; }
.burst-factor-list span { padding: 2px 5px; border-radius: 3px; background: #f5f7fa; color: #606266; font-size: 11px; }
.burst-factor-list .burst-penalty { background: #fff1f0; color: #d03050; }
.burst-empty { color: #909399; font-size: 13px; }
.stock-signal-dialog { min-height: 360px; }
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
.card-actions { display: flex; align-items: center; gap: 8px; }
.column-order-panel { display: grid; gap: 4px; }
.column-order-title { padding: 2px 4px 6px; color: #606266; font-size: 12px; font-weight: 700; }
.column-order-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  padding: 3px 4px;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: grab;
  user-select: none;
}
.column-order-item:hover { background: #f5f7fa; border-color: #e4e7ed; }
.column-drag-handle { color: #a8abb2; font-size: 15px; }
.column-order-item > span { flex: 1; color: #303133; font-size: 13px; }
.column-order-actions { display: flex; align-items: center; gap: 2px; }
.stock-filter-badge :deep(.el-badge__content) { transform: translateY(-45%) translateX(55%); }
.stock-filter-panel { display: flex; flex-direction: column; gap: 12px; }
.stock-filter-summary { color: #606266; font-size: 13px; }
.stock-filter-form :deep(.el-form-item) { margin-bottom: 12px; }
.stock-filter-form :deep(.el-select) { width: 100%; }
.stock-filter-section-title { padding-top: 4px; color: #303133; font-size: 13px; font-weight: 700; }
.number-filter-row {
  display: grid;
  grid-template-columns: 94px minmax(0, 1fr) 18px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
}
.number-filter-row > span:first-child { color: #606266; font-size: 12px; }
.number-filter-row :deep(.el-input-number) { width: 100%; }
.number-filter-divider { color: #909399; text-align: center; font-size: 12px; }
.stock-filter-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 6px; }
.main-table { width: 100%; }
.stock-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.stock-chips .el-tag { cursor: pointer; }
.stock-chart-trigger { padding: 0; border: 0; background: transparent; color: #303133; font: inherit; cursor: pointer; }
.stock-chart-trigger:hover { color: #409eff; text-decoration: underline; }
.text-up { color: #d03050; font-weight: 600; }
.text-down { color: #059669; font-weight: 600; }
.risk-text { color: #c45656; font-size: 12px; }
.recommend-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  font-weight: 700;
  background: #f5f7fa;
  color: #606266;
}
.recommend-count.count-strong {
  background: #fff1f0;
  color: #d03050;
}
.recommend-count.count-mid {
  background: #fff7e6;
  color: #b7791f;
}
.recommend-count.count-low {
  background: #f4f4f5;
  color: #909399;
}

@media (max-width: 1180px) {
  .rule-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .page-header { flex-direction: column; }
  .market-gate { align-items: flex-start; flex-direction: column; }
  .gate-metrics { justify-content: flex-start; }
  .burst-candidate-item { grid-template-columns: 24px minmax(140px, 1fr) auto auto; }
  .burst-factor-list { grid-column: 2 / -1; }
}

/* 今日推荐面板 */
.daily-recommend-band {
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 12px 16px; margin-bottom: 12px;
}
.daily-recommend-band.dr-empty { background: #fafafa; }
.dr-header {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; margin-bottom: 10px;
}
.dr-snapshot { font-size: 12px; color: #909399; }
.dr-cards { display: flex; gap: 10px; }
.dr-card {
  flex: 1; background: #fafbfc; border-radius: 6px; padding: 10px;
  border-left: 4px solid #909399;
}
.dr-card.dr-rank-1 { border-left-color: #d03050; background: #fff5f5; }
.dr-card.dr-rank-2 { border-left-color: #e6a23c; background: #fef9f0; }
.dr-card.dr-rank-3 { border-left-color: #409eff; background: #f0f7ff; }
.dr-rank-badge { font-size: 11px; font-weight: 700; color: #909399; margin-bottom: 2px; }
.dr-rank-1 .dr-rank-badge { color: #d03050; }
.dr-rank-2 .dr-rank-badge { color: #e6a23c; }
.dr-rank-3 .dr-rank-badge { color: #409eff; }
.dr-code-name { display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
.dr-code-name .stock-link { font-weight: 700; font-size: 14px; }
.dr-name { font-size: 12px; color: #606266; }
.dr-theme { font-size: 12px; color: #909399; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.dr-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 8px; }
.dr-metric { display: flex; justify-content: space-between; align-items: center; font-size: 11px; }
.dr-m-label { color: #909399; }
.dr-m-val { font-weight: 600; color: #303133; }
.dr-m-val.dr-score { color: #d03050; font-size: 13px; }
</style>
