<template>
  <div class="indicators-page">
    <!-- 工具栏 -->
    <el-card shadow="never" class="toolbar">
      <el-row :gutter="12" align="middle">
        <el-col :span="4">
          <el-input v-model="code" placeholder="股票代码" clearable @keyup.enter="load" />
        </el-col>
        <el-col :span="4">
          <el-date-picker
            v-model="date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width:100%"
          />
        </el-col>
        <el-col :span="4">
          <el-input v-model="stockName" placeholder="股票名称（可选）" clearable />
        </el-col>
        <el-col :span="4">
          <el-button type="primary" :loading="loading" @click="load">加载K线</el-button>
        </el-col>
        <el-col :span="8" style="text-align:right;display:flex;align-items:center;justify-content:flex-end;gap:8px">
          <el-button
            v-if="code"
            size="small"
            :icon="PictureFilled"
            @click="openMinuteChart"
          >分时图</el-button>
          <el-radio-group v-model="period" size="small" @change="load" v-if="code">
            <el-radio-button value="daily">日K</el-radio-button>
            <el-radio-button value="weekly">周K</el-radio-button>
            <el-radio-button value="monthly">月K</el-radio-button>
          </el-radio-group>
        </el-col>
      </el-row>
    </el-card>

    <!-- 错误提示 -->
    <el-alert v-if="errMsg" :title="errMsg" type="error" show-icon closable @close="errMsg=''" />

    <!-- 图表区域 -->
    <el-card shadow="never" class="chart-card" v-loading="loading">
      <div v-if="!chartReady && !loading" class="empty-hint">
        <el-empty description="输入股票代码并选择日期后点击加载" />
      </div>
      <div v-show="chartReady">
        <div class="chart-title">{{ code }} {{ stockName }} — K线图（{{ periodLabel }}）</div>
        <div ref="candleRef" class="chart-container" style="height:360px" />
        <div class="chart-subtitle">成交量</div>
        <div ref="volumeRef" class="chart-container" style="height:120px" />
        <div class="chart-subtitle">MA 均线（5 / 10 / 20 / 60）</div>
        <div ref="maRef" class="chart-container" style="height:120px" />
      </div>
    </el-card>
    <!-- 分时图弹窗 -->
    <el-dialog
      v-model="minuteVisible"
      :title="`${code} ${stockName} — 分时图（${date}）`"
      width="860px"
      :close-on-click-modal="true"
      destroy-on-close
    >
      <div class="minute-wrap">
        <!-- 用新浪财经分时图图片接口，实时刷新 -->
        <div class="minute-img-row">
          <img
            :src="minuteImgUrl"
            alt="分时图"
            class="minute-img"
            @error="onImgError"
          />
        </div>
        <div class="minute-tip">
          <el-text type="info" size="small">
            数据来源：新浪财经 · 仅展示最新交易日分时图
          </el-text>
          <el-button size="small" text type="primary" @click="refreshMinute">刷新</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { PictureFilled } from '@element-plus/icons-vue'
import axios from 'axios'

const route = useRoute()

const code       = ref('')
const date       = ref('')
const stockName  = ref('')
const period     = ref('daily')
const loading    = ref(false)
const chartReady = ref(false)
const errMsg     = ref('')

const candleRef = ref(null)
const volumeRef = ref(null)
const maRef     = ref(null)

let candleChart = null
let volumeChart = null
let maChart     = null
let resizeOb    = null

const periodLabel = computed(() =>
  ({ daily: '日K', weekly: '周K', monthly: '月K' }[period.value])
)

// ── 分时图 ────────────────────────────────────────────────────────────────
const minuteVisible = ref(false)
const minuteTs      = ref(Date.now())

/**
 * 将股票代码转为新浪财经所需前缀格式
 * 上交所: sh + code，深交所: sz + code，北交所: bj + code
 */
function toSinaCode(raw) {
  const c = raw.trim()
  if (/^6/.test(c))          return `sh${c}`
  if (/^[089]/.test(c))      return `sz${c}`
  if (/^[34]/.test(c))       return `sz${c}`
  if (/^[78]/.test(c))       return `bj${c}`
  return `sz${c}`
}

const minuteImgUrl = computed(() => {
  if (!code.value) return ''
  const sc = toSinaCode(code.value)
  // 新浪财经分时图图片接口（加时间戳防缓存）
  return `https://image.sinajs.cn/newchart/min/n/${sc}.gif?t=${minuteTs.value}`
})

function openMinuteChart() {
  if (!code.value) { ElMessage.warning('请先输入股票代码'); return }
  minuteTs.value = Date.now()
  minuteVisible.value = true
}

function refreshMinute() {
  minuteTs.value = Date.now()
}

function onImgError() {
  ElMessage.warning('分时图加载失败，可能是非交易时段或网络限制')
}

// ── 颜色 ──────────────────────────────────────────────────────────────────
const UP_COLOR   = '#ef5350'
const DOWN_COLOR = '#26a69a'
const MA_COLORS  = ['#2196f3', '#ff9800', '#9c27b0', '#4caf50']
const MA_PERIODS = [5, 10, 20, 60]
const MA_NAMES   = ['MA5', 'MA10', 'MA20', 'MA60']

// ── 动态加载 lightweight-charts ───────────────────────────────────────────
async function getLWC() {
  const lc = await import('lightweight-charts')
  return lc
}

// ── 计算 MA ───────────────────────────────────────────────────────────────
function calcMA(closes, n) {
  return closes.map((_, i) => {
    if (i < n - 1) return null
    const sum = closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0)
    return parseFloat((sum / n).toFixed(3))
  })
}

// ── 同步时间轴 ─────────────────────────────────────────────────────────────
function syncTimeScale(charts) {
  let syncing = false
  charts.forEach((src, si) => {
    src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return
      syncing = true
      charts.forEach((dst, di) => {
        if (di !== si) dst.timeScale().setVisibleLogicalRange(range)
      })
      syncing = false
    })
  })
}

// ── 自适应宽度 ─────────────────────────────────────────────────────────────
function startResize(refs, charts) {
  if (resizeOb) resizeOb.disconnect()
  resizeOb = new ResizeObserver(() => {
    refs.forEach((r, i) => {
      if (r.value && charts[i]) {
        charts[i].applyOptions({ width: r.value.clientWidth })
      }
    })
  })
  refs.forEach(r => { if (r.value) resizeOb.observe(r.value) })
}

// ── 销毁图表 ──────────────────────────────────────────────────────────────
function destroyCharts() {
  if (resizeOb) { resizeOb.disconnect(); resizeOb = null }
  ;[candleChart, volumeChart, maChart].forEach(c => {
    if (c) { try { c.remove() } catch (_) {} }
  })
  candleChart = volumeChart = maChart = null
}

// ── 构建图表 ──────────────────────────────────────────────────────────────
async function buildCharts(raw) {
  const lc = await getLWC()
  const { createChart, ColorType, CrosshairMode, LineStyle,
          CandlestickSeries, HistogramSeries, LineSeries } = lc

  const w = candleRef.value ? candleRef.value.clientWidth : 800

  function baseOpts(height) {
    return {
      width: w,
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#666666',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#f0f0f0' },
        horzLines: { color: '#f0f0f0' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: '#e0e0e0',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#e0e0e0',
        timeVisible: false,
        rightOffset: 5,
        barSpacing: 8,
      },
      handleScroll: true,
      handleScale: true,
    }
  }

  const candleData = raw.map(d => ({
    time:  d.date,
    open:  +d.open  || 0,
    high:  +d.high  || 0,
    low:   +d.low   || 0,
    close: +d.close || 0,
  }))

  const volData = raw.map(d => ({
    time:  d.date,
    value: +d.volume || 0,
    color: (+d.close >= +d.open) ? UP_COLOR + 'aa' : DOWN_COLOR + 'aa',
  }))

  const closes = raw.map(d => +d.close || 0)
  const maDatasets = MA_PERIODS.map(n => {
    const arr = calcMA(closes, n)
    return arr
      .map((v, i) => v === null ? null : { time: raw[i].date, value: v })
      .filter(Boolean)
  })

  // 1. K线主图
  candleChart = createChart(candleRef.value, baseOpts(360))
  const cs = candleChart.addSeries(CandlestickSeries, {
    upColor: UP_COLOR, downColor: DOWN_COLOR,
    borderUpColor: UP_COLOR, borderDownColor: DOWN_COLOR,
    wickUpColor: UP_COLOR, wickDownColor: DOWN_COLOR,
  })
  cs.setData(candleData)
  maDatasets.forEach((ds, i) => {
    const s = candleChart.addSeries(LineSeries, {
      color: MA_COLORS[i], lineWidth: 1,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: true,
      title: MA_NAMES[i],
    })
    s.setData(ds)
  })

  // 2. 成交量子图
  volumeChart = createChart(volumeRef.value, {
    ...baseOpts(120),
    rightPriceScale: { borderColor: '#e0e0e0', scaleMargins: { top: 0.1, bottom: 0 } },
  })
  const vs = volumeChart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'right',
  })
  vs.setData(volData)

  // 3. MA 独立子图
  maChart = createChart(maRef.value, {
    ...baseOpts(120),
    rightPriceScale: { borderColor: '#e0e0e0', scaleMargins: { top: 0.1, bottom: 0.1 } },
  })
  maDatasets.forEach((ds, i) => {
    const s = maChart.addSeries(LineSeries, {
      color: MA_COLORS[i], lineWidth: 1,
      priceLineVisible: false, title: MA_NAMES[i],
    })
    s.setData(ds)
  })

  syncTimeScale([candleChart, volumeChart, maChart])
  startResize([candleRef, volumeRef, maRef], [candleChart, volumeChart, maChart])
  ;[candleChart, volumeChart, maChart].forEach(c => c.timeScale().fitContent())
}

// ── 加载数据 ──────────────────────────────────────────────────────────────
async function load() {
  if (!code.value)  { ElMessage.warning('请输入股票代码'); return }
  if (!date.value)  { ElMessage.warning('请选择日期');    return }

  loading.value  = true
  errMsg.value   = ''
  destroyCharts()
  chartReady.value = false

  try {
    const res = await axios.get('/instock/api_data/kline', {
      params: { code: code.value, date: date.value, period: period.value },
    })
    const raw = res.data
    if (!Array.isArray(raw) || raw.length === 0) {
      ElMessage.warning('暂无K线数据（该股票无历史数据）')
      return
    }
    chartReady.value = true
    await nextTick()
    await buildCharts(raw)
  } catch (e) {
    errMsg.value = '加载失败：' + (e.response?.data?.error || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  code.value      = route.query.code || ''
  date.value      = route.query.date || new Date().toISOString().slice(0, 10)
  stockName.value = route.query.name || ''
  if (code.value && date.value) load()
})

onUnmounted(destroyCharts)
</script>

<style scoped>
.indicators-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar :deep(.el-card__body) { padding: 10px 16px; }
.chart-card :deep(.el-card__body) { padding: 12px 16px; }

.chart-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.chart-subtitle {
  font-size: 12px;
  color: #909399;
  margin: 10px 0 6px;
}
.chart-container {
  width: 100%;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  overflow: hidden;
}
.empty-hint { padding: 60px 0; text-align: center; }

/* 分时图弹窗 */
.minute-wrap { display: flex; flex-direction: column; gap: 8px; }
.minute-img-row { display: flex; justify-content: center; background: #fafafa; border-radius: 4px; padding: 8px; }
.minute-img { max-width: 100%; height: auto; border-radius: 2px; }
.minute-tip { display: flex; align-items: center; justify-content: space-between; padding: 0 2px; }
</style>
