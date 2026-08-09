<template>
  <section class="stock-card">
    <header class="stock-head">
      <div>
        <div class="stock-title">
          <el-button
            v-if="clickableCode"
            link
            type="primary"
            class="code-btn"
            @click="$emit('open-indicators', row)"
          >
            {{ row.code }}
          </el-button>
          <span v-else class="stock-code">{{ row.code }}</span>
          <span>{{ row.name }}</span>
          <el-tag size="small" type="warning">{{ row.trade_theme || row.best_sector || '未分类' }} #{{ row.sector_rank || '-' }}</el-tag>
          <el-tag v-if="row.mainline_theme" size="small" type="primary" effect="plain">
            {{ row.mainline_theme }}
          </el-tag>
          <el-tag v-if="row.signal_type" size="small" effect="plain">{{ row.signal_type }}</el-tag>
        </div>
        <div class="stock-sub">
          主线强势 {{ row.sector_strong_count || 0 }} 只，前三均涨 {{ fmtPct(row.sector_top3_avg_ret) }}，
          昨日 {{ row.prev_date || row.prev_d || '-' }}
        </div>
      </div>
      <div class="head-actions">
        <el-select
          v-if="editableTheme"
          v-model="row._dominantDraft"
          filterable
          allow-create
          default-first-option
          size="small"
          class="sector-editor"
          :loading="row._sectorLoading"
          @focus="$emit('load-theme-options', row)"
          @change="$emit('save-theme', row)"
        >
          <el-option
            v-for="s in row._sectorOptions || [row.trade_theme || row.best_sector].filter(Boolean)"
            :key="s"
            :label="s"
            :value="s"
          />
        </el-select>
        <div class="score-box">
          <span>{{ activeMode === 'mainline' ? '核心分' : '评分' }}</span>
          <b>{{ fmtNum(row.score, 1) }}</b>
        </div>
      </div>
    </header>

    <div class="metric-grid" :class="{ 'metric-grid-compact': metricFields.length <= 2 }">
      <Metric v-if="showMetric('buy_price')" label="买入价" :value="fmtPrice(row.buy_price)" />
      <Metric v-if="showMetric('morning_ret')" label="早盘涨幅" :value="fmtPct(row.ret_vs_prevclose)" :tone="tone(row.ret_vs_prevclose)" />
      <Metric v-if="showMetric('position')" label="区间位置" :value="fmtPct(row.pos_in_range)" />
      <Metric v-if="showMetric('pullback')" label="回撤" :value="fmtPct(row.pullback)" tone="green" />
      <Metric v-if="showMetric('volume')" :label="volumeLabel" :value="fmtRatio(row.amt_vs_prev)" />
      <Metric v-if="showMetric('efficiency')" label="推进效率" :value="fmtNum(row.push_efficiency, 2)" />
      <Metric v-if="showMetric('distance_30d')" label="距30日高" :value="fmtPct(row.distance_to_30d_high)" />
      <Metric v-if="showMetric('day_high')" label="日内最高" :value="fmtPct(row.day_max_up_pct)" :tone="tone(row.day_max_up_pct)" />
      <Metric v-if="showMetric('day_close')" label="日内收盘" :value="fmtPct(row.day_close_return_pct)" :tone="tone(row.day_close_return_pct)" />
      <Metric v-if="showMetric('next_open')" label="次日开盘" :value="fmtPct(row.next_open_return_pct)" :tone="tone(row.next_open_return_pct)" />
      <Metric v-if="showMetric('next_1000')" label="次日10点" :value="fmtPct(row.next_1000_return_pct)" :tone="tone(row.next_1000_return_pct)" />
      <Metric v-if="showMetric('next_sell')" label="次日卖点" :value="fmtPct(row.next_1000_max_up_pct)" :tone="tone(row.next_1000_max_up_pct)" />
    </div>

    <div class="tag-line">
      <el-tag
        v-for="tag in splitTags(row.tags)"
        :key="tag"
        size="small"
        :type="tag.includes('风险') || tag.includes('过热') || tag.includes('回落') ? 'danger' : 'info'"
        effect="plain"
      >
        {{ tag }}
      </el-tag>
    </div>

    <MinuteBarChart
      :code="row.code"
      :date="date"
      :bars="row.today_bars || []"
      :compare-date="row.prev_date || row.prev_d"
      :compare-bars="row.prev_bars || []"
      :daily-bars="dailyBars"
      :focus-time="focusTime"
    />
  </section>
</template>

<script setup>
import { defineComponent, h } from 'vue'
import MinuteBarChart from '@/components/MinuteBarChart.vue'

const props = defineProps({
  row: { type: Object, required: true },
  date: { type: String, default: '' },
  activeMode: { type: String, default: 'strict' },
  volumeLabel: { type: String, default: '量能同比' },
  metricFields: {
    type: Array,
    default: () => ['buy_price', 'morning_ret', 'position', 'pullback', 'volume', 'efficiency', 'distance_30d', 'day_high', 'day_close', 'next_open', 'next_1000', 'next_sell'],
  },
  dailyBars: { type: Array, default: () => [] },
  focusTime: { type: String, default: "" },
  editableTheme: { type: Boolean, default: false },
  clickableCode: { type: Boolean, default: false },
})

defineEmits(['open-indicators', 'load-theme-options', 'save-theme'])

const Metric = defineComponent({
  props: {
    label: String,
    value: String,
    tone: String,
  },
  setup(props) {
    return () => h('div', { class: ['metric', props.tone ? `metric-${props.tone}` : ''] }, [
      h('span', props.label),
      h('b', props.value || '-'),
    ])
  },
})

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

function fmtPrice(v) {
  return fmtNum(v, 2)
}

function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '-'
  return `${fmtNum(v, 2)}%`
}

function fmtRatio(v) {
  if (v === null || v === undefined || v === '') return '-'
  return `${fmtNum(v, 2)}x`
}

function tone(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'red' : 'green'
}

function showMetric(key) {
  return props.metricFields.includes(key)
}

function observeTone(label) {
  if (label === '可买') return 'success'
  if (label === '偏追高') return 'danger'
  return 'info'
}

function splitTags(tags) {
  return String(tags || '').split(',').map(s => s.trim()).filter(Boolean).slice(0, 8)
}
</script>

<style scoped>
.stock-card {
  background: #fff;
  border: 1px solid #ebeef5;
  padding: 12px;
}

.stock-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.stock-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: #303133;
  font-size: 15px;
  font-weight: 700;
}

.code-btn {
  font-size: 15px;
  font-weight: 700;
  padding: 0;
}

.stock-code {
  color: #409eff;
  font-weight: 700;
}

.stock-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sector-editor {
  width: 170px;
}

.score-box {
  min-width: 76px;
  text-align: right;
}

.score-box span {
  display: block;
  font-size: 12px;
  color: #909399;
}

.score-box b {
  color: #f56c6c;
  font-size: 22px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(86px, 1fr));
  gap: 8px;
  margin-bottom: 8px;
}

.metric-grid-compact {
  grid-template-columns: repeat(2, minmax(180px, 260px));
}

.metric {
  border: 1px solid #f0f2f5;
  background: #fafafa;
  padding: 7px 8px;
  min-height: 50px;
}

.metric span {
  display: block;
  margin-bottom: 5px;
  color: #909399;
  font-size: 11px;
}

.metric b {
  color: #303133;
  font-size: 14px;
}

.metric-red b {
  color: #f56c6c;
}

.metric-green b {
  color: #67c23a;
}

.tag-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0 4px;
}

@media (max-width: 900px) {
  .stock-head {
    display: grid;
  }

  .head-actions {
    justify-content: space-between;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
