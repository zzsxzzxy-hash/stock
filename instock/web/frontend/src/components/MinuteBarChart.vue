<template>
  <div class="mbc-wrap">
    <!-- 工具栏 -->
    <div class="mbc-toolbar">
      <el-radio-group v-model="chartType" size="small" @change="renderChart">
        <el-radio-button value="price">分时图</el-radio-button>
        <el-radio-button value="vol">量柱图</el-radio-button>
        <el-radio-button value="both">分时+量柱</el-radio-button>
        <el-radio-button value="daily">日K</el-radio-button>
      </el-radio-group>

      <div v-if="chartType !== 'daily'" class="compare-area">
        <span class="cmp-label">叠加对比：</span>
        <el-date-picker
          v-model="cmpDate"
          type="date"
          value-format="YYYY-MM-DD"
          size="small"
          placeholder="选择对比日期"
          style="width:135px"
          @change="loadCmpBars"
        />
        <el-button v-if="cmpDate" size="small" type="danger" text @click="clearCmp">清除</el-button>
      </div>
      <el-tag v-if="loading" size="small" type="info" effect="plain">加载中…</el-tag>
    </div>

    <!-- 图表 -->
    <div ref="chartEl" class="mbc-chart" />

    <!-- 图例 -->
    <div class="mbc-legend">
      <span class="legend-dot" style="background:#5470c6" />
      <span>{{ props.date }}（查询日）</span>
      <template v-if="cmpDate && cmpBars.length">
        <span class="legend-dot" style="background:#ee6666; margin-left:16px" />
        <span>{{ cmpDate }}（对比日）</span>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'

const props = defineProps({
  code: { type: String, required: true },
  date: { type: String, required: true },
  bars: { type: Array,  default: () => [] },
  compareDate: { type: String, default: '' },
  compareBars: { type: Array, default: () => [] },
  dailyBars: { type: Array, default: () => [] },
  focusTime: { type: String, default: '' },
})

const chartType = ref('both')
const cmpDate   = ref(props.compareDate || '')
const cmpBars   = ref(props.compareBars || [])
const loading   = ref(false)
const chartEl   = ref(null)
let chart = null
let resizeObserver = null

// ── 交易时间轴 ────────────────────────────────────────────────────────────
function tradingTimes() {
  const t = []
  for (let h = 9; h <= 15; h++)
    for (let m = 0; m < 60; m++) {
      const s = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`
      if ((s >= '09:31' && s <= '11:30') || (s >= '13:01' && s <= '15:00')) t.push(s)
    }
  return t
}

const ALL_TIMES = tradingTimes()

// ── 将 bars 数组转成 time→bar map ─────────────────────────────────────────
function toMap(bars) {
  const m = {}
  for (const b of (bars || [])) if (b?.time) m[b.time] = b
  return m
}

// ── 计算分时涨跌幅序列 + 量序列 ──────────────────────────────────────────
function detectBase(bars) {
  const sorted = [...(bars || [])].sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')))
  const preOpen = sorted.find(b => b?.time && b.time <= '09:30' && Number(b.pre_close) > 0)
  if (preOpen) return Number(preOpen.pre_close)
  const firstStable = sorted.find(b => b?.time && b.time < '09:31' && Number(b.close) > 0)
  if (firstStable) return Number(firstStable.close)
  const first = sorted.find(b => Number(b.pre_close || b.open || b.close) > 0)
  return first ? Number(first.pre_close || first.open || first.close) : null
}

function calcSeries(bars) {
  const map    = toMap(bars)
  const prices = []
  const vols   = []
  const base = detectBase(bars)
  for (const t of ALL_TIMES) {
    const b = map[t]
    if (b) {
      prices.push(base ? +((b.close - base) / base * 100).toFixed(3) : null)
      vols.push(+(b.volume || 0))
    } else {
      prices.push(null)
      vols.push(null)
    }
  }
  return { prices, vols }
}

function renderDailyChart() {
  const bars = [...(props.dailyBars || [])]
    .filter(bar => bar?.date)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .slice(-100)
  if (!bars.length) {
    chart.clear()
    return
  }
  const dates = bars.map(bar => String(bar.date).slice(5))
  const candles = bars.map(bar => [Number(bar.open), Number(bar.close), Number(bar.low), Number(bar.high)])
  const volumes = bars.map(bar => ({
    value: Number(bar.volume) || 0,
    itemStyle: { color: Number(bar.close) >= Number(bar.open) ? '#d84a4a' : '#1b9a74' },
  }))
  chart.setOption({
    animation: false,
    backgroundColor: '#fff',
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      formatter(params) {
        const bar = bars[params?.[0]?.dataIndex]
        if (!bar) return ''
        return [
          `<b>${bar.date}</b>`,
          `开 ${Number(bar.open).toFixed(2)}　收 ${Number(bar.close).toFixed(2)}`,
          `高 ${Number(bar.high).toFixed(2)}　低 ${Number(bar.low).toFixed(2)}`,
          `涨跌 ${Number(bar.pct_chg).toFixed(2)}%`,
        ].join('<br/>')
      },
    },
    grid: [{ top: 24, left: 54, right: 18, height: '56%' }, { top: '71%', left: 54, right: 18, bottom: 30 }],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', gridIndex: 1, data: dates, boundaryGap: true, axisLabel: { fontSize: 10 }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } }, axisLabel: { fontSize: 10, formatter: value => Number(value).toFixed(2) } },
      { gridIndex: 1, scale: true, splitLine: { show: false }, axisLabel: { fontSize: 10, formatter: value => value >= 10000 ? `${(value / 10000).toFixed(0)}万` : value } },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], zoomOnMouseWheel: false }],
    series: [
      { type: 'candlestick', data: candles, itemStyle: { color: '#d84a4a', color0: '#1b9a74', borderColor: '#d84a4a', borderColor0: '#1b9a74' } },
      { type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes, barMaxWidth: 12 },
    ],
  }, true)
}

// ── 加载对比日 ─────────────────────────────────────────────────────────────
async function loadCmpBars() {
  if (!cmpDate.value || !props.code) return
  loading.value = true
  try {
    const res = await axios.get('/api/minute_bars', { params: { code: props.code, date: cmpDate.value } })
    cmpBars.value = res.data.bars || []
  } catch { cmpBars.value = [] }
  finally { loading.value = false }
  renderChart()
}

function clearCmp() {
  cmpDate.value = ''
  cmpBars.value = []
  renderChart()
}

// ── ECharts 渲染 ───────────────────────────────────────────────────────────
function renderChart() {
  if (!chart) return
  if (chartType.value === 'daily') {
    renderDailyChart()
    return
  }
  const main   = calcSeries(props.bars)
  const hasCmp = cmpBars.value.length > 0
  const cmp    = hasCmp ? calcSeries(cmpBars.value) : null

  const showPrice = chartType.value !== 'vol'
  const showVol   = chartType.value !== 'price'
  const splitVol  = showPrice && showVol  // 分时+量柱 → 上下两格

  const grids  = []
  const xAxes  = []
  const yAxes  = []
  const series = []

  // X轴标签只在关键节点显示
  const keyTimes = new Set(['09:31','10:00','10:30','11:00','11:30','13:01','13:30','14:00','14:30','15:00'])
  const xLabelFmt = v => keyTimes.has(v) ? v : ''

  if (splitVol) {
    grids.push({ top: 28, left: 52, right: 16, height: '54%' })
    grids.push({ top: '68%', left: 52, right: 16, bottom: 28 })
  } else {
    grids.push({ top: 28, left: 52, right: 16, bottom: 28 })
  }

  const gridN = grids.length
  for (let i = 0; i < gridN; i++) {
    xAxes.push({
      type: 'category', data: ALL_TIMES, gridIndex: i,
      axisLabel: { fontSize: 10, interval: 0, formatter: xLabelFmt, rotate: 0 },
      axisLine:  { lineStyle: { color: '#e0e0e0' } },
      axisTick:  { show: false },
      splitLine: { show: false },
    })
    yAxes.push({
      type: 'value', gridIndex: i, scale: true,
      axisLabel: { fontSize: 10, formatter: i === 0 && showPrice ? v => v + '%' : v => v >= 1000 ? (v/10000).toFixed(1)+'万' : v },
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLine:  { show: false },
    })
  }

  // ─ 分时线 ─
  if (showPrice) {
    series.push({
      name: `${props.date}`, type: 'line',
      xAxisIndex: 0, yAxisIndex: 0,
      data: main.prices,
      smooth: false, symbol: 'none',
      lineStyle: { color: '#5470c6', width: 1.5 },
      areaStyle: { color: { type: 'linear', x:0,y:0,x2:0,y2:1,
        colorStops: [{offset:0,color:'rgba(84,112,198,0.18)'},{offset:1,color:'rgba(84,112,198,0.01)'}] }},
      connectNulls: false,
      markLine: {
        silent: true, symbol: 'none',
        data: (() => {
          const lines = [{ yAxis: 0, lineStyle: { color: '#aaa', type: 'dashed', width: 1 } }]
          if (props.focusTime) {
            lines.push({ xAxis: props.focusTime, label: { show: true, formatter: props.focusTime, position: 'start', fontSize: 10, color: '#d03050' }, lineStyle: { color: '#d03050', type: 'dashed', width: 1.5 } })
          }
          return lines
        })(),
      },
    })
    if (cmp) {
      series.push({
        name: `${cmpDate.value}`, type: 'line',
        xAxisIndex: 0, yAxisIndex: 0,
        data: cmp.prices,
        smooth: false, symbol: 'none',
        lineStyle: { color: '#ee6666', width: 1.5, type: 'dashed' },
        areaStyle: { color: 'rgba(238,102,102,0.07)' },
        connectNulls: false,
      })
    }
  }

  // ─ 量柱 ─
  const vi = splitVol ? 1 : 0
  if (showVol) {
    series.push({
      name: `${props.date}量`, type: 'bar',
      xAxisIndex: vi, yAxisIndex: vi,
      data: main.vols,
      itemStyle: { color: 'rgba(84,112,198,0.65)' },
      barMaxWidth: 6, barCategoryGap: '10%',
    })
    if (cmp) {
      series.push({
        name: `${cmpDate.value}量`, type: 'bar',
        xAxisIndex: vi, yAxisIndex: vi,
        data: cmp.vols,
        itemStyle: { color: 'rgba(238,102,102,0.55)' },
        barMaxWidth: 6, barGap: '-100%',
      })
    }
  }

  chart.setOption({
    animation: false,
    backgroundColor: '#fff',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: '#999' } },
      textStyle: { fontSize: 11 },
      formatter(params) {
        if (!params?.length) return ''
        const t = params[0].axisValue
        let h = `<div style="font-weight:600;margin-bottom:4px">${t}</div>`
        for (const p of params) {
          if (p.value === null || p.value === undefined) continue
          const isPrice = !p.seriesName.endsWith('量')
          const val = isPrice ? p.value + '%' : Number(p.value).toLocaleString()
          h += `${p.marker}<span style="color:#666">${p.seriesName}</span>: <b>${val}</b><br/>`
        }
        return h
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: Array.from({length: gridN},(_,i)=>i),
        start: 0,
        end: 100,
        zoomOnMouseWheel: false,
        moveOnMouseWheel: false,
        moveOnMouseMove: false,
      },
    ],
    grid: grids, xAxis: xAxes, yAxis: yAxes, series,
  }, true)

  if (props.focusTime && ALL_TIMES.includes(props.focusTime)) {
    setTimeout(() => {
      const idx = ALL_TIMES.indexOf(props.focusTime)
      chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: idx })
    }, 200)
  }
}

watch(() => props.bars, () => nextTick(renderChart), { deep: true })
watch(() => props.date,  () => {
  cmpDate.value = props.compareDate || ''
  cmpBars.value = props.compareBars || []
  nextTick(renderChart)
})
watch(() => props.compareDate, v => {
  cmpDate.value = v || ''
  nextTick(renderChart)
})
watch(() => props.compareBars, v => {
  cmpBars.value = v || []
  nextTick(renderChart)
}, { deep: true })
watch(() => props.dailyBars, () => nextTick(renderChart), { deep: true })

onMounted(() => {
  chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  const resizeFn = () => chart?.resize()
  window.addEventListener('resize', resizeFn)
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartEl.value)
  onUnmounted(() => {
    resizeObserver?.disconnect()
    chart?.dispose()
    window.removeEventListener('resize', resizeFn)
  })
  nextTick(renderChart)
})
</script>

<style scoped>
.mbc-wrap { width: 100%; }
.mbc-toolbar {
  display: flex; align-items: center; gap: 12px; padding: 6px 0 4px; flex-wrap: wrap;
}
.compare-area { display: flex; align-items: center; gap: 6px; }
.cmp-label    { font-size: 12px; color: #606266; white-space: nowrap; }
.mbc-chart    { width: 100%; height: 300px; }
.mbc-legend   { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #909399; padding: 3px 0; }
.legend-dot   { width: 12px; height: 4px; border-radius: 2px; display: inline-block; }
</style>
