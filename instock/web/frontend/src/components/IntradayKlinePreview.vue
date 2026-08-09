<template>
  <section class="preview-card">
    <header class="preview-head">
      <div>
        <strong>{{ name }} {{ code }}</strong>
        <span>{{ date }} 全日走势</span>
      </div>
      <el-radio-group v-model="chartMode" size="small" @change="renderChart">
        <el-radio-button value="price">分时图</el-radio-button>
        <el-radio-button value="kline">分钟K线</el-radio-button>
      </el-radio-group>
    </header>
    <div ref="chartEl" class="preview-chart" />
    <div v-if="!visibleBars.length" class="preview-empty">暂无可用分钟K线</div>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  code: { type: String, required: true },
  name: { type: String, default: '' },
  date: { type: String, default: '' },
  bars: { type: Array, default: () => [] },
})

const chartEl = ref(null)
const chartMode = ref('kline')
let chart = null

const visibleBars = computed(() => [...(props.bars || [])]
  .filter(bar => String(bar?.time || '').slice(0, 5) >= '09:30')
  .sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')))
)

function asNumber(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function renderChart() {
  if (!chart) return
  const bars = visibleBars.value
  if (!bars.length) {
    chart.clear()
    return
  }

  const times = bars.map(bar => String(bar.time || '').slice(0, 5))
  const candles = bars.map(bar => [
    asNumber(bar.open),
    asNumber(bar.close),
    asNumber(bar.low),
    asNumber(bar.high),
  ])
  const prevClose = asNumber(bars.find(bar => asNumber(bar.pre_close))?.pre_close)
  const changes = bars.map(bar => {
    const close = asNumber(bar.close)
    return prevClose && close ? +((close / prevClose - 1) * 100).toFixed(3) : null
  })
  const volumes = bars.map(bar => {
    const open = asNumber(bar.open) || 0
    const close = asNumber(bar.close) || 0
    return {
      value: asNumber(bar.volume) || 0,
      itemStyle: { color: close >= open ? '#d84a4a' : '#1b9a74' },
    }
  })
  const keyTimes = new Set(['09:31', '10:00', '10:30', '11:00', '11:30', '13:01', '13:30', '14:00', '14:30', '15:00'])

  const isPriceChart = chartMode.value === 'price'
  const primarySeries = isPriceChart ? {
    type: 'line',
    data: changes,
    smooth: false,
    symbol: 'none',
    lineStyle: { color: '#5470c6', width: 1.6 },
    areaStyle: { color: 'rgba(84,112,198,0.12)' },
    markLine: {
      silent: true,
      symbol: 'none',
      label: { show: false },
      lineStyle: { color: '#909399', type: 'dashed' },
      data: [{ yAxis: 0 }],
    },
  } : {
    type: 'candlestick',
    data: candles,
    itemStyle: {
      color: '#d84a4a',
      color0: '#1b9a74',
      borderColor: '#d84a4a',
      borderColor0: '#1b9a74',
    },
    markLine: prevClose ? {
      silent: true,
      symbol: 'none',
      label: { fontSize: 10, formatter: '昨收 {c}' },
      lineStyle: { color: '#909399', type: 'dashed' },
      data: [{ yAxis: prevClose }],
    } : undefined,
  }

  chart.setOption({
    animation: false,
    backgroundColor: '#fff',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter(params) {
        const index = params?.[0]?.dataIndex
        const bar = bars[index]
        if (!bar) return ''
        const open = asNumber(bar.open)
        const close = asNumber(bar.close)
        const high = asNumber(bar.high)
        const low = asNumber(bar.low)
        const change = prevClose && close ? ((close / prevClose - 1) * 100).toFixed(2) : '-'
        return [
          `<b>${times[index]}</b>`,
          `开 ${open?.toFixed(2) || '-'}　收 ${close?.toFixed(2) || '-'}`,
          `高 ${high?.toFixed(2) || '-'}　低 ${low?.toFixed(2) || '-'}`,
          `涨跌 ${change === '-' ? '-' : `${change}%`}`,
        ].join('<br/>')
      },
    },
    grid: [
      { left: 54, right: 16, top: 26, height: '57%' },
      { left: 54, right: 16, top: '72%', bottom: 26 },
    ],
    xAxis: [
      {
        type: 'category',
        data: times,
        boundaryGap: true,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#dcdfe6' } },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: times,
        boundaryGap: true,
        axisLabel: { fontSize: 10, formatter: value => keyTimes.has(value) ? value : '' },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#dcdfe6' } },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: '#f0f2f5' } },
        axisLabel: {
          fontSize: 10,
          formatter: value => isPriceChart ? `${Number(value).toFixed(1)}%` : Number(value).toFixed(2),
        },
      },
      {
        gridIndex: 1,
        scale: true,
        splitLine: { show: false },
        axisLabel: { fontSize: 10, formatter: value => value >= 10000 ? `${(value / 10000).toFixed(0)}万` : value },
      },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], zoomOnMouseWheel: false }],
    series: [
      primarySeries,
      {
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barMaxWidth: 8,
      },
    ],
  }, true)
}

const resizeChart = () => chart?.resize()

watch(visibleBars, () => nextTick(renderChart), { deep: true })
onMounted(() => {
  chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  window.addEventListener('resize', resizeChart)
  nextTick(renderChart)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.preview-card { width: 510px; }
.preview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-height: 22px;
  color: #303133;
  font-size: 13px;
}
.preview-head > div:first-child { display: flex; align-items: baseline; gap: 8px; }
.preview-head span { color: #909399; font-size: 11px; white-space: nowrap; }
.preview-chart { width: 100%; height: 280px; }
.preview-empty { padding: 64px 0; text-align: center; color: #909399; font-size: 13px; }
</style>
