<template>
  <div class="rank-row" :class="{ selected }" @click="$emit('click')">
    <!-- 第一行：基础信息 + 评分 -->
    <div class="row-top">
      <div class="stock-info">
        <span class="stock-code">{{ item.code }}</span>
        <span class="stock-name">{{ item.name }}</span>
        <el-tag v-for="s in item.sectors?.slice(0,2)" :key="s"
                size="small" type="info" class="sector-tag">{{ s }}</el-tag>
      </div>
      <div class="score-box" :class="scoreClass">
        <span class="score-val">{{ scoreDisplay }}</span>
        <span class="score-label">分</span>
      </div>
    </div>

    <!-- 第二行：量能标签 + 效率方向 -->
    <div class="row-mid">
      <el-tag :type="labelType" size="small" class="vol-label">{{ item.vol_label?.text || '—' }}</el-tag>
      <span class="eff-icon" :class="`eff-${item.eff_dir}`">
        {{ effIcon }}
      </span>
      <span class="price-info" :class="item.today_change > 0 ? 'up' : item.today_change < 0 ? 'down' : ''">
        {{ item.current_price }} &nbsp;
        {{ item.today_change > 0 ? '+' : '' }}{{ item.today_change?.toFixed(2) }}%
      </span>
    </div>

    <!-- 第三行：量比数据 -->
    <div class="row-stats">
      <span class="stat-item">
        <span class="stat-label">实时量比</span>
        <span class="stat-val" :class="item.rt_vol_ratio >= 2 ? 'high' : 'mid'">
          {{ item.rt_vol_ratio?.toFixed(2) }}x
        </span>
      </span>
      <span class="stat-item" v-if="item.price_slope !== 0">
        <span class="stat-label">涨速</span>
        <span class="stat-val" :class="item.price_slope > 0 ? 'up' : 'down'">
          {{ item.price_slope > 0 ? '+' : '' }}{{ item.price_slope?.toFixed(3) }}%/m
        </span>
      </span>
      <span class="stat-item">
        <span class="stat-label">位置</span>
        <el-tag :type="posTagType" size="small">{{ posLabel }}</el-tag>
      </span>
    </div>

    <!-- 第四行：迷你分钟对比图 -->
    <div class="mini-chart-wrap">
      <div :ref="el => { miniChartEl = el }" class="mini-chart"></div>
    </div>

    <!-- 因子得分细节 -->
    <div class="factor-row">
      <span class="factor" title="位置因子">A:{{ item.fa }}</span>
      <span class="factor" title="效率因子">B:{{ item.fb }}</span>
      <span class="factor" :class="item.fc === null ? 'accumulating' : ''" title="量能因子">
        C:{{ item.fc === null ? '…' : item.fc }}
      </span>
      <span class="factor" title="板块因子">D:{{ item.fd }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  item:         { type: Object, required: true },
  selected:     { type: Boolean, default: false },
  miniChartKey: { type: Number, default: 0 },  // 变化时刷新图表
})
defineEmits(['click'])

const miniChartEl = ref(null)
let   ec = null

const scoreDisplay = computed(() => {
  if (props.item.score <= -99) return '剔除'
  return props.item.score?.toFixed(1)
})

const scoreClass = computed(() => {
  const s = props.item.score
  if (s >= 7) return 'score-high'
  if (s >= 4) return 'score-mid'
  if (s >= 2) return 'score-low'
  return 'score-bad'
})

const labelType = computed(() => {
  const t = props.item.vol_label?.type
  if (t === 'purple') return 'danger'
  if (t === 'red')    return 'warning'
  if (t === 'gray')   return 'info'
  return 'success'
})

const effIcon = computed(() => {
  return { up: '↑', down: '↓', flat: '→' }[props.item.eff_dir] || '→'
})

const posLabel = computed(() =>
  ({ low: '低位', break: '突破', high: '高位', other: '中性' }[props.item.position] || '—')
)
const posTagType = computed(() =>
  ({ low: 'success', break: 'warning', high: 'danger', other: 'info' }[props.item.position] || 'info')
)

function renderMiniChart() {
  if (!miniChartEl.value) return
  if (!ec) {
    ec = echarts.init(miniChartEl.value)
  } else {
    ec.clear()
  }
  const todayVols = (props.item.today_minute_vols || []).map(b => b.vol)
  const yestVols  = (props.item.yest_minute_vols  || []).map(b => b.vol)
  const times     = (props.item.today_minute_vols || []).map(b => b.time)

  ec.setOption({
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: 'category', data: times, show: false },
    yAxis: { type: 'value', show: false },
    series: [
      { type: 'bar', data: yestVols,  itemStyle: { color: 'rgba(103,194,58,0.6)' }, barGap: '-100%' },
      { type: 'bar', data: todayVols, itemStyle: { color: 'rgba(245,108,108,0.8)' } },
    ],
  })
}

watch(() => props.miniChartKey, () => renderMiniChart())
onMounted(() => renderMiniChart())
onBeforeUnmount(() => ec?.dispose())
</script>

<style scoped>
.rank-row {
  padding: 10px 12px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.15s;
}
.rank-row:hover { background: #f5f7fa; }
.rank-row.selected { background: #ecf5ff; border-left: 3px solid #409eff; }

.row-top { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 4px; }
.stock-info { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.stock-code { font-weight: 700; font-size: 13px; color: #303133; }
.stock-name { font-size: 12px; color: #606266; }
.sector-tag { font-size: 10px; }

.score-box { display: flex; align-items: baseline; gap: 2px; padding: 2px 8px; border-radius: 12px; flex-shrink: 0; }
.score-val { font-weight: 700; font-size: 16px; }
.score-label { font-size: 11px; }
.score-high { background: #fef0f0; color: #f56c6c; }
.score-mid  { background: #fdf6ec; color: #e6a23c; }
.score-low  { background: #f0f9eb; color: #67c23a; }
.score-bad  { background: #f4f4f5; color: #909399; }

.row-mid { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.vol-label { font-size: 11px; }
.eff-icon { font-size: 14px; font-weight: 700; }
.eff-up   { color: #f56c6c; }
.eff-down { color: #67c23a; }
.eff-flat { color: #909399; }
.price-info { font-size: 12px; margin-left: auto; }
.up   { color: #f56c6c; }
.down { color: #67c23a; }

.row-stats { display: flex; gap: 12px; margin-bottom: 6px; }
.stat-item { display: flex; align-items: center; gap: 3px; }
.stat-label { font-size: 11px; color: #909399; }
.stat-val { font-size: 11px; font-weight: 600; }
.stat-val.high { color: #f56c6c; }
.stat-val.mid  { color: #e6a23c; }
.stat-val.up   { color: #f56c6c; }
.stat-val.down { color: #67c23a; }

.mini-chart-wrap { width: 100%; height: 36px; margin-bottom: 4px; }
.mini-chart { width: 100%; height: 100%; }

.factor-row { display: flex; gap: 8px; }
.factor { font-size: 11px; color: #909399; background: #f5f5f5; padding: 1px 5px; border-radius: 4px; }
.accumulating { color: #e6a23c; background: #fdf6ec; }
</style>
