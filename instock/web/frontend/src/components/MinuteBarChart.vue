<template>
  <div class="mbc-wrap">
    <!-- 工具栏 -->
    <div class="mbc-toolbar">
      <el-radio-group v-model="chartType" size="small" @change="renderChart">
        <el-radio-button value="price">分时图</el-radio-button>
        <el-radio-button value="vol">量柱图</el-radio-button>
        <el-radio-button value="both">分时+量柱</el-radio-button>
      </el-radio-group>

      <div class="compare-area">
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
})

const chartType = ref('both')
const cmpDate   = ref('')
const cmpBars   = ref([])
const loading   = ref(false)
const chartEl   = ref(null)
let chart = null

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
function calcSeries(bars) {
  const map    = toMap(bars)
  const prices = []
  const vols   = []
  let base = null
  for (const t of ALL_TIMES) {
    const b = map[t]
    if (b) {
      if (base === null) base = b.pre_close || b.open || b.close
      prices.push(base ? +((b.close - base) / base * 100).toFixed(3) : null)
      vols.push(+(b.volume || 0))
    } else {
      prices.push(null)
      vols.push(null)
    }
  }
  return { prices, vols }
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
        data: [{ yAxis: 0, lineStyle: { color: '#aaa', type: 'dashed', width: 1 } }],
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
      { type: 'inside', xAxisIndex: Array.from({length: gridN},(_,i)=>i), start: 0, end: 100 },
    ],
    grid: grids, xAxis: xAxes, yAxis: yAxes, series,
  }, true)
}

watch(() => props.bars, () => nextTick(renderChart), { deep: true })
watch(() => props.date,  () => { cmpDate.value=''; cmpBars.value=[]; nextTick(renderChart) })

onMounted(() => {
  chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  const resizeFn = () => chart?.resize()
  window.addEventListener('resize', resizeFn)
  onUnmounted(() => { chart?.dispose(); window.removeEventListener('resize', resizeFn) })
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
