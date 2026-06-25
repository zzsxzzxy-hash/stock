<template>
  <div class="vm-page">
    <!-- 三栏布局 -->
    <div class="vm-layout">

      <!-- ═══ 左栏：过滤器 ═══ -->
      <div class="vm-left">
        <el-card shadow="never" class="filter-card">
          <div class="filter-title">选股范围</div>

          <!-- 日期选择 -->
          <div class="filter-section">
            <div class="filter-label">查询日期</div>
            <el-date-picker
              v-model="queryDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="默认最新数据"
              size="small"
              style="width:100%"
              @change="fetchRank(true)"
            />
            <div class="filter-desc" v-if="rankDate">当前数据：{{ rankDate }}</div>
          </div>

          <!-- 市场过滤 -->
          <div class="filter-section">
            <div class="filter-label">市场过滤</div>
            <el-checkbox-group v-model="marketFilter" @change="fetchRank(true)" class="market-checkbox-group">
              <el-checkbox label="sh"  >沪市A股</el-checkbox>
              <el-checkbox label="sz"  >深市A股</el-checkbox>
              <el-checkbox label="cyb" >创业板</el-checkbox>
              <el-checkbox label="kcb" >科创板</el-checkbox>
              <el-checkbox label="bj"  >京市A股</el-checkbox>
            </el-checkbox-group>
            <div class="filter-desc" v-if="!marketFilter.length">未选择 = 全市场</div>
          </div>

          <!-- 位置过滤 -->
          <div class="filter-section">
            <div class="filter-label">位置过滤</div>
            <el-radio-group v-model="positionFilter" @change="fetchRank(true)" class="filter-radio-group">
              <el-radio value="all" class="radio-item">
                <span class="radio-label">不限制</span>
                <span class="radio-desc">全市场扫描</span>
              </el-radio>
              <el-radio value="low" class="radio-item">
                <span class="radio-label">仅低位</span>
                <span class="radio-desc">价格≤120日均线×1.05</span>
              </el-radio>
              <el-radio value="break" class="radio-item">
                <span class="radio-label">仅突破</span>
                <span class="radio-desc">价格突破120日新高</span>
              </el-radio>
            </el-radio-group>
          </div>

          <!-- 涨跌过滤 -->
          <div class="filter-section">
            <div class="filter-label">涨跌过滤</div>
            <el-radio-group v-model="changeFilter" @change="fetchRank(true)" size="small">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="up">
                <span style="color:#f56c6c">红盘</span>
              </el-radio-button>
              <el-radio-button value="down">
                <span style="color:#67c23a">绿盘</span>
              </el-radio-button>
            </el-radio-group>
          </div>

          <!-- 量比阈值 -->
          <div class="filter-section">
            <div class="filter-label">量比阈值</div>
            <div class="threshold-row">
              <el-slider v-model="volThreshold" :min="1.0" :max="5.0" :step="0.1"
                         :marks="{1.5:'1.5', 2:'2', 3:'3'}"
                         @change="fetchRank" />
              <span class="threshold-val">{{ volThreshold.toFixed(1) }}x</span>
            </div>
          </div>

          <!-- 因子筛选 -->
          <div class="filter-section">
            <div class="filter-label-row">
              <span class="filter-label">因子筛选</span>
              <el-radio-group v-model="factorMode" size="small" @change="fetchRank(true)">
                <el-radio-button value="and">全部满足</el-radio-button>
                <el-radio-button value="or">任一满足</el-radio-button>
              </el-radio-group>
            </div>
            <div class="factor-filter-grid">
              <div v-for="f in factorDefs" :key="f.key" class="factor-filter-row">
                <el-checkbox
                  v-model="f.enabled"
                  @change="fetchRank(true)"
                  class="factor-checkbox"
                >
                  <span :class="`factor-label factor-${f.key}`">{{ f.key }}</span>
                  <span class="factor-name-s">{{ f.name }}</span>
                </el-checkbox>
                <el-input-number
                  v-if="f.enabled"
                  v-model="f.minVal"
                  :min="-10" :max="10" :step="0.5" :precision="1"
                  size="small"
                  style="width:80px"
                  @change="fetchRank(true)"
                />
                <span v-else class="factor-off">—</span>
              </div>
            </div>
            <div class="filter-desc">开启后仅显示该因子 ≥ 设定分值的股票</div>
          </div>

          <!-- 板块联动 -->
          <div class="filter-section">
            <div class="filter-label-row">
              <span class="filter-label">板块联动</span>
              <el-switch v-model="sectorGroup" @change="fetchRank" />
            </div>
            <div class="filter-desc" v-if="sectorGroup">按板块分组，优先显示集团军</div>
          </div>

          <!-- 高风险剔除 -->
          <div class="filter-section">
            <div class="filter-label-row">
              <span class="filter-label">高位滞涨剔除</span>
              <el-switch v-model="excludeHighRisk" @change="fetchRank" />
            </div>
            <div class="filter-desc">自动排除高位巨量滞涨</div>
          </div>

          <!-- 状态信息 -->
          <div class="status-box" :class="statusClass">
            <div class="status-dot" :class="statusClass"></div>
            <span>{{ statusText }}</span>
          </div>

          <!-- 自动刷新 -->
          <div class="filter-section">
            <el-button
              :type="autoRefresh ? 'danger' : 'success'"
              size="small" block style="width:100%"
              @click="toggleAutoRefresh"
            >
              {{ autoRefresh ? '停止自动刷新' : '开启自动刷新(30s)' }}
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- ═══ 中栏：动态监控列表 ═══ -->
      <div class="vm-middle">
        <el-card shadow="never" class="rank-card">
          <template #header>
            <div class="rank-header">
              <span class="rank-title">量能异动排行</span>
              <div class="rank-meta">
                <el-text type="info" size="small">{{ rankTime || '—' }} 共{{ rankData.length }}只</el-text>
                <el-button :icon="Refresh" size="small" :loading="loading" @click="fetchRank(true)">刷新</el-button>
                <el-button :icon="RefreshRight" size="small" @click="refreshMiniCharts">刷新图表</el-button>
              </div>
            </div>
          </template>

          <div v-if="loading && !rankData.length" class="empty-tip">
            <el-icon class="rotating"><Loading /></el-icon> 计算中...
          </div>
          <div v-else-if="!rankData.length" class="empty-tip">
            <el-empty description="暂无异动信号，等待数据积累" :image-size="80" />
          </div>

          <!-- 板块分组模式 -->
          <template v-else-if="sectorGroup">
            <div v-for="(group, sector) in groupedData" :key="sector" class="sector-group">
              <div class="sector-group-title">
                <el-tag type="warning" size="small">{{ sector }}</el-tag>
                <span class="sector-count">{{ group.length }}只异动</span>
              </div>
              <RankRow
                v-for="item in group" :key="item.code"
                :item="item"
                :selected="selectedCode === item.code"
                :mini-chart-key="miniChartKey"
                @click="selectStock(item)"
              />
            </div>
          </template>

          <!-- 普通列表模式 -->
          <template v-else>
            <RankRow
              v-for="item in rankData" :key="item.code"
              :item="item"
              :selected="selectedCode === item.code"
              :mini-chart-key="miniChartKey"
              @click="selectStock(item)"
            />
          </template>
        </el-card>
      </div>

      <!-- ═══ 右栏：个股深度 ═══ -->
      <div class="vm-right">
        <el-card shadow="never" class="detail-card" v-if="selectedCode">
          <template #header>
            <div class="detail-header">
              <span class="detail-title">{{ selectedName }}（{{ selectedCode }}）</span>
              <el-tag :type="posTagType(selectedItem?.position)" size="small">
                {{ posLabel(selectedItem?.position) }}
              </el-tag>
            </div>
          </template>

          <div v-if="detailLoading" class="detail-loading">
            <el-icon class="rotating"><Loading /></el-icon> 加载中...
          </div>
          <template v-else-if="detail">
            <!-- 触发次数 -->
            <div class="trigger-summary">
              <el-statistic title="今日触发次数" :value="detail.trigger_count" />
              <el-text type="info" size="small" style="margin-left:12px">
                触发越多，信号越可靠
              </el-text>
            </div>

            <!-- 分时对比图 -->
            <div class="detail-section-title">今日 vs 昨日分时对比</div>
            <div ref="chartTimeline" class="chart-box"></div>

            <!-- 近10日效率走势 -->
            <div class="detail-section-title">近10日量价效率走势</div>
            <div ref="chartEfficiency" class="chart-box chart-box-sm"></div>

            <!-- K线全景图 -->
            <div class="detail-section-title">K线全景（近60日）</div>
            <div ref="chartKline" class="chart-box"></div>

            <!-- 信号时间轴 -->
            <div class="detail-section-title">
              信号时间轴
              <el-tag size="small" type="danger" style="margin-left:8px">
                共触发 {{ detail.trigger_count }} 次
              </el-tag>
            </div>
            <div class="signal-timeline">
              <div v-if="!detail.signal_timeline.length" class="empty-tip-sm">今日暂无信号</div>
              <div v-for="sig in detail.signal_timeline" :key="sig.time" class="signal-item"
                   :class="sig.change > 0 ? 'sig-up' : sig.change < 0 ? 'sig-down' : ''">
                <span class="sig-time">{{ sig.time }}</span>
                <span class="sig-ratio">分钟量比 {{ sig.min_ratio }}x</span>
                <span class="sig-change">{{ sig.change > 0 ? '+' : '' }}{{ sig.change }}%</span>
              </div>
            </div>
          </template>
        </el-card>

        <div v-else class="detail-placeholder">
          <el-empty description="点击左侧股票查看深度分析" :image-size="80" />
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Refresh, RefreshRight, Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import * as echarts from 'echarts'
import RankRow from '@/components/VolumeRankRow.vue'

// ── 过滤器状态 ─────────────────────────────────────────────
const positionFilter  = ref('all')
const volThreshold    = ref(1.5)
const sectorGroup     = ref(false)
const excludeHighRisk = ref(true)
const queryDate       = ref('')
const marketFilter    = ref([])
const changeFilter    = ref('all')   // all / up / down

// ── 因子筛选 ───────────────────────────────────────────────
const factorMode = ref('and')
const factorDefs = ref([
  { key: 'fa', name: '位置', enabled: false, minVal: 1 },
  { key: 'fb', name: '效率', enabled: false, minVal: 1 },
  { key: 'fc', name: '量能', enabled: false, minVal: 1 },
  { key: 'fd', name: '板块', enabled: false, minVal: 1 },
])

// ── 排行榜数据 ─────────────────────────────────────────────
const loading    = ref(false)
const rankData   = ref([])
const rankTime   = ref('')
const rankDate   = ref('')         // 接口实际返回的日期
const miniChartKey = ref(0)

// ── 选中股票 ───────────────────────────────────────────────
const selectedCode = ref('')
const selectedName = ref('')
const selectedItem = ref(null)
const detailLoading = ref(false)
const detail        = ref(null)

// ── 图表 refs ──────────────────────────────────────────────
const chartTimeline   = ref(null)
const chartEfficiency = ref(null)
const chartKline      = ref(null)
let   ecTimeline = null, ecEfficiency = null, ecKline = null

// ── 自动刷新 ───────────────────────────────────────────────
const autoRefresh = ref(false)
let   refreshTimer = null

const statusClass = computed(() => autoRefresh.value ? 'status-active' : 'status-idle')
const statusText  = computed(() => autoRefresh.value ? '实时监控中' : '已停止')

// ── 板块分组 ───────────────────────────────────────────────
const groupedData = computed(() => {
  if (!sectorGroup.value) return {}
  const groups = {}
  for (const item of rankData.value) {
    const s = item.sectors?.[0] || '未分类'
    if (!groups[s]) groups[s] = []
    groups[s].push(item)
  }
  // 按组内数量降序
  return Object.fromEntries(
    Object.entries(groups).sort((a, b) => b[1].length - a[1].length)
  )
})

// ── 拉取排行榜 ─────────────────────────────────────────────
async function fetchRank(force = false) {
  loading.value = true
  try {
    const params = {
      position:      positionFilter.value,
      vol_threshold: volThreshold.value.toFixed(1),
      refresh:       force ? '1' : '0',
      market:        marketFilter.value.length ? marketFilter.value.join(',') : 'all',
      change:        changeFilter.value,
      factor_mode:   factorMode.value,
    }
    if (queryDate.value) params.date = queryDate.value
    // 启用的因子过滤
    for (const f of factorDefs.value) {
      if (f.enabled) params[`${f.key}_min`] = f.minVal
    }
    const res = await axios.get('/api/volume_rank', { params })
    rankData.value = res.data.data || []
    rankTime.value = res.data.time || ''
    rankDate.value = res.data.date || ''
  } catch (e) {
    ElMessage.error('排行榜加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function refreshMiniCharts() {
  miniChartKey.value++
}

// ── 选中股票 → 加载深度数据 ────────────────────────────────
function selectStock(item) {
  selectedCode.value = item.code
  selectedName.value = item.name
  selectedItem.value = item
  loadDetail(item.code)
}

async function loadDetail(code) {
  detailLoading.value = true
  detail.value = null
  try {
    const res = await axios.get('/api/volume_detail', { params: { code } })
    detail.value = res.data
    await nextTick()
    renderDetailCharts(res.data)
  } catch (e) {
    ElMessage.error('深度数据加载失败')
  } finally {
    detailLoading.value = false
  }
}

// ── 渲染右栏图表 ───────────────────────────────────────────
function renderDetailCharts(d) {
  renderTimeline(d)
  renderEfficiency(d)
  renderKline(d)
}

function renderTimeline(d) {
  if (!chartTimeline.value) return
  if (!ecTimeline) ecTimeline = echarts.init(chartTimeline.value)
  const todayTimes  = d.today_timeline.map(b => b.time)
  const todayPrices = d.today_timeline.map(b => b.close)
  const yestPrices  = d.yest_timeline.map(b => b.close)
  const todayVols   = d.today_timeline.map(b => b.volume)
  const yestVols    = d.yest_timeline.map(b => b.volume)

  ecTimeline.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['今日价格', '昨日价格'], top: 4 },
    grid: [
      { left: 50, right: 20, top: 36, bottom: '40%' },
      { left: 50, right: 20, top: '62%', bottom: 30 },
    ],
    xAxis: [
      { type: 'category', data: todayTimes, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: todayTimes, gridIndex: 1 },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true },
      { type: 'value', gridIndex: 1 },
    ],
    series: [
      { name: '今日价格', type: 'line', data: todayPrices, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#f56c6c' }, symbol: 'none' },
      { name: '昨日价格', type: 'line', data: yestPrices,  xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#67c23a', type: 'dashed' }, symbol: 'none' },
      { name: '今日量',   type: 'bar',  data: todayVols,  xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: '#f56c6c88' } },
      { name: '昨日量',   type: 'bar',  data: yestVols,   xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: '#67c23a88' } },
    ],
  })
}

function renderEfficiency(d) {
  if (!chartEfficiency.value) return
  if (!ecEfficiency) ecEfficiency = echarts.init(chartEfficiency.value)
  const dates = d.efficiency_trend.map(e => e.date.slice(5))
  const vals  = d.efficiency_trend.map(e => e.efficiency)
  ecEfficiency.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 16, bottom: 30 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', scale: true },
    series: [{
      type: 'line', data: vals, smooth: true,
      lineStyle: { color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.15)' },
      symbol: 'circle', symbolSize: 5,
      markLine: { data: [{ type: 'average', name: '均值' }] },
    }],
  })
}

function renderKline(d) {
  if (!chartKline.value) return
  if (!ecKline) ecKline = echarts.init(chartKline.value)
  const pi    = d.position_info
  const dates = d.kline_data.map(k => k.date.slice(5))
  const ohlc  = d.kline_data.map(k => [k.open, k.close, k.low, k.high])
  const vols  = d.kline_data.map(k => ({
    value: k.volume,
    itemStyle: { color: k.close >= k.open ? '#f56c6c88' : '#67c23a88' }
  }))

  // 色带：标注位置区间
  const markAreas = []
  if (pi.ma120 > 0) {
    markAreas.push([{ yAxis: 0 }, { yAxis: pi.ma120 * 1.05 }])
  }

  ecKline.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 55, right: 20, top: 16, bottom: '32%' },
      { left: 55, right: 20, top: '70%', bottom: 30 },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1 },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true },
      { type: 'value', gridIndex: 1 },
    ],
    series: [
      {
        type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: '#f56c6c', color0: '#67c23a', borderColor: '#f56c6c', borderColor0: '#67c23a' },
        markLine: {
          data: [
            pi.ma120   ? { yAxis: pi.ma120,   name: 'MA120', lineStyle: { color: '#e6a23c' } } : null,
            pi.high120 ? { yAxis: pi.high120, name: '120高', lineStyle: { color: '#f56c6c', type: 'dashed' } } : null,
          ].filter(Boolean),
        },
        markArea: pi.ma120 > 0 ? {
          silent: true,
          data: [[{ yAxis: 0 }, { yAxis: pi.ma120 * 1.05 }]],
          itemStyle: { color: 'rgba(103,194,58,0.08)' },
        } : undefined,
      },
      { type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1 },
    ],
  })
}

// ── 辅助 ───────────────────────────────────────────────────
function posLabel(pos) {
  return { low: '低位', break: '突破', high: '高位', other: '中性' }[pos] || '—'
}
function posTagType(pos) {
  return { low: 'success', break: 'warning', high: 'danger', other: 'info' }[pos] || 'info'
}

// ── 自动刷新 ───────────────────────────────────────────────
function toggleAutoRefresh() {
  if (autoRefresh.value) {
    clearInterval(refreshTimer)
    refreshTimer = null
    autoRefresh.value = false
  } else {
    autoRefresh.value = true
    refreshTimer = setInterval(() => fetchRank(false), 30000)
  }
}

onMounted(() => fetchRank())
onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  ecTimeline?.dispose()
  ecEfficiency?.dispose()
  ecKline?.dispose()
})
</script>

<style scoped>
.vm-page { height: 100%; }
.vm-layout {
  display: grid;
  grid-template-columns: 220px 1fr 320px;
  gap: 12px;
  height: calc(100vh - 84px);
}

/* 左栏 */
.vm-left { overflow-y: auto; }
.filter-card :deep(.el-card__body) { padding: 12px; }
.filter-title { font-weight: 700; font-size: 13px; margin-bottom: 12px; color: #303133; }
.filter-section { margin-bottom: 16px; }
.filter-label { font-size: 12px; color: #606266; margin-bottom: 6px; font-weight: 600; }
.filter-label-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.filter-desc { font-size: 11px; color: #909399; }
.filter-radio-group { display: flex; flex-direction: column; gap: 6px; }
.radio-item { display: flex; flex-direction: column; margin-right: 0 !important; height: auto; }
.radio-label { font-size: 12px; }
.radio-desc { font-size: 11px; color: #909399; margin-left: 22px; }
.threshold-row { display: flex; align-items: center; gap: 8px; }
.threshold-row .el-slider { flex: 1; }
.threshold-val { font-size: 12px; font-weight: 700; color: #409eff; min-width: 30px; }
.status-box { display: flex; align-items: center; gap: 6px; padding: 8px; border-radius: 6px;
  font-size: 12px; margin-bottom: 12px; }
.status-active { background: #f0f9eb; color: #67c23a; }
.status-idle { background: #f4f4f5; color: #909399; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-active .status-dot { background: #67c23a; animation: pulse 1.5s infinite; }
.status-idle .status-dot { background: #c0c4cc; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

/* 中栏 */
.vm-middle { overflow-y: auto; }
.rank-card { height: 100%; }
.rank-card :deep(.el-card__body) { padding: 0; overflow-y: auto; max-height: calc(100vh - 140px); }
.rank-header { display: flex; align-items: center; justify-content: space-between; }
.rank-title { font-weight: 700; font-size: 14px; }
.rank-meta { display: flex; align-items: center; gap: 8px; }
.empty-tip { padding: 40px; text-align: center; color: #909399; }
.sector-group { margin-bottom: 8px; }
.sector-group-title { display: flex; align-items: center; gap: 8px; padding: 6px 12px;
  background: #fafafa; border-bottom: 1px solid #f0f0f0; }
.sector-count { font-size: 12px; color: #909399; }

/* 右栏 */
.vm-right { overflow-y: auto; }
.detail-card :deep(.el-card__body) { padding: 12px; }
.detail-header { display: flex; align-items: center; gap: 8px; }
.detail-title { font-weight: 700; font-size: 13px; }
.detail-loading { padding: 40px; text-align: center; color: #909399; }
.trigger-summary { display: flex; align-items: center; margin-bottom: 12px;
  padding: 8px; background: #fef0f0; border-radius: 6px; }
.detail-section-title { font-size: 12px; font-weight: 600; color: #606266;
  margin: 12px 0 6px; padding-left: 6px; border-left: 3px solid #409eff; }
.chart-box { width: 100%; height: 220px; }
.chart-box-sm { height: 120px; }
.detail-placeholder { height: 100%; display: flex; align-items: center; justify-content: center; }

/* 信号时间轴 */
.signal-timeline { max-height: 200px; overflow-y: auto; }
.empty-tip-sm { font-size: 12px; color: #909399; padding: 8px; }
.signal-item { display: flex; align-items: center; gap: 12px; padding: 5px 8px;
  border-bottom: 1px solid #f5f5f5; font-size: 12px; }
.sig-time { color: #606266; min-width: 36px; }
.sig-ratio { color: #e6a23c; font-weight: 600; }
.sig-change { min-width: 48px; text-align: right; font-weight: 600; }
.sig-up .sig-change { color: #f56c6c; }
.sig-down .sig-change { color: #67c23a; }

.market-checkbox-group { display: flex; flex-direction: column; gap: 4px; }
.market-checkbox-group :deep(.el-checkbox) { height: 24px; margin-right: 0; }
.market-checkbox-group :deep(.el-checkbox__label) { font-size: 12px; }

/* 因子筛选 */
.factor-filter-grid { display: flex; flex-direction: column; gap: 6px; margin: 6px 0 4px; }
.factor-filter-row {
  display: flex; align-items: center; justify-content: space-between;
  background: #fafafa; border-radius: 4px; padding: 3px 6px;
  border: 1px solid #f0f0f0;
}
.factor-checkbox { flex: 1; }
.factor-checkbox :deep(.el-checkbox__label) { display: flex; align-items: center; gap: 4px; }
.factor-label {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 3px;
  font-size: 11px; font-weight: 700; color: #fff;
}
.factor-fa { background: #409eff; }
.factor-fb { background: #67c23a; }
.factor-fc { background: #e6a23c; }
.factor-fd { background: #f56c6c; }
.factor-name-s { font-size: 11px; color: #606266; }
.factor-off { font-size: 12px; color: #c0c4cc; min-width: 80px; text-align: right; }

.rotating { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
</style>
