<template>
  <div class="single-wrap">
    <!-- 输入区 -->
    <el-card shadow="never" class="input-card">
      <el-form inline @submit.prevent="calculate">
        <el-form-item label="股票代码">
          <el-input
            v-model="form.code"
            placeholder="如 000001"
            style="width:120px"
            maxlength="6"
            clearable
            @keyup.enter="calculate"
          />
        </el-form-item>

        <el-form-item label="日期">
          <el-date-picker
            v-model="form.date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width:145px"
          />
        </el-form-item>

        <el-form-item label="时间">
          <el-time-picker
            v-model="form.timeVal"
            format="HH:mm"
            value-format="HH:mm"
            :disabled-seconds="() => Array.from({length:60},(_,i)=>i)"
            style="width:110px"
            placeholder="09:31"
          />
        </el-form-item>

        <el-form-item label="启用因子">
          <el-checkbox-group v-model="form.factors">
            <el-checkbox label="A">位置因子A</el-checkbox>
            <el-checkbox label="B">效率因子B</el-checkbox>
            <el-checkbox label="C">量能因子C</el-checkbox>
            <el-checkbox label="D">板块因子D</el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="loading" @click="calculate">
            计算得分
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 结果区 -->
    <div v-if="result" class="result-wrap">
      <!-- 股票信息 + 总分 -->
      <div class="summary-row">
        <div class="stock-info">
          <span class="stock-code">{{ result.code }}</span>
          <span class="stock-name">{{ result.name }}</span>
          <el-tag type="info" size="small">{{ result.date }} {{ result.hhmm }}</el-tag>
          <el-tag :type="posTagType(result.summary.position)" size="small">
            {{ posLabel(result.summary.position) }}
          </el-tag>
        </div>
        <div class="total-score-box" :class="totalScoreClass">
          <div class="total-label">综合得分</div>
          <div class="total-value">{{ result.summary.score }}</div>
          <div v-if="result.summary.is_veto" class="veto-badge">一票否决</div>
        </div>
      </div>

      <!-- 快捷指标 -->
      <el-row :gutter="10" class="metric-row">
        <el-col :span="4" v-for="m in metrics" :key="m.label">
          <div class="metric-card">
            <div class="metric-label">{{ m.label }}</div>
            <div class="metric-value" :style="m.color ? {color: m.color} : {}">{{ m.value }}</div>
          </div>
        </el-col>
      </el-row>

      <!-- 龙头强势同款单股票详情 -->
      <StockSignalDetail
        v-if="result.signal_detail"
        class="signal-detail"
        :row="result.signal_detail"
        :date="result.date"
        active-mode="strict"
      />

      <!-- 预计算数据 -->
      <el-card shadow="never" class="section-card" v-if="!result.pre_calc.available">
        <el-alert type="warning" title="预计算缓存(pre_calc)不存在，已从数据库临时补算，部分数据可能不准" :closable="false" />
      </el-card>

      <!-- 四因子详情 -->
      <el-row :gutter="12" class="factors-row">
        <el-col :span="12" v-for="fk in ['A','B','C','D']" :key="fk">
          <el-card shadow="hover" class="factor-detail-card">
            <template #header>
              <div class="fcard-header">
                <el-tag :type="fTagType(fk)" effect="dark" size="large" class="f-badge">{{ fk }}</el-tag>
                <span class="f-name">{{ fName(fk) }}</span>
                <div class="f-score-box" :class="scoreClass(fResult(fk).score)">
                  <span class="f-score">{{ fResult(fk).score ?? '-' }}</span>
                  <span class="f-score-label">分</span>
                </div>
              </div>
            </template>

            <!-- 未启用 / 跳过 -->
            <div v-if="fResult(fk).skipped || !fResult(fk).enabled" class="skipped-tip">
              <el-icon><InfoFilled /></el-icon>
              {{ fResult(fk).enabled === false ? '因子已禁用' : (fResult(fk).reason || '未计算') }}
            </div>

            <template v-else>
              <!-- 命中规则 -->
              <div class="hit-rule-row" v-if="fResult(fk).hit_rule">
                <span class="hit-label">命中规则</span>
                <el-tag :type="scoreClass(fResult(fk).score)" size="small" effect="light">
                  {{ fResult(fk).hit_rule }}
                </el-tag>
                <span class="score-def-text">
                  {{ scoreDefText(fResult(fk).score_def) }}
                </span>
              </div>

              <!-- 一票否决详情（因子A） -->
              <template v-if="fk === 'A' && fResult(fk).veto && fResult(fk).veto.triggered !== undefined">
                <el-divider content-position="left" class="mini-divider">一票否决条件</el-divider>
                <div v-for="(v, k) in vetoLines(fResult(fk).veto)" :key="k" class="step-row veto-row">
                  <span class="step-name">{{ k }}</span>
                  <span class="step-val" :class="vetoValClass(String(v))">{{ v }}</span>
                </div>
              </template>

              <!-- 板块列表（因子D） -->
              <template v-if="fk === 'D' && fResult(fk).my_sectors?.length">
                <el-divider content-position="left" class="mini-divider">所属板块</el-divider>
                <el-table :data="fResult(fk).my_sectors" size="small" :show-header="true">
                  <el-table-column prop="sector" label="板块" />
                  <el-table-column prop="signal_count" label="信号股数" width="85" align="center" />
                  <el-table-column prop="avg_change" label="均涨幅" width="80" align="center">
                    <template #default="{ row }">
                      <span :class="row.avg_change >= 0 ? 'text-up' : 'text-down'">
                        {{ row.avg_change > 0 ? '+' : '' }}{{ row.avg_change }}%
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
                <div v-if="!fResult(fk).cache_source" class="cache-hint">
                  <el-icon><WarningFilled /></el-icon> 无排行榜缓存，板块信号数均为0
                </div>
              </template>

              <!-- 计算步骤 -->
              <el-divider content-position="left" class="mini-divider">计算过程</el-divider>
              <div v-for="step in fResult(fk).steps" :key="step.name" class="step-row">
                <span class="step-name">{{ step.name }}</span>
                <span class="step-val" :class="stepValClass(step)">
                  {{ step.value }}{{ step.unit ? ' ' + step.unit : '' }}
                </span>
              </div>
            </template>
          </el-card>
        </el-col>
      </el-row>

      <!-- 原始 JSON（折叠）-->
      <el-collapse style="margin-top:12px">
        <el-collapse-item title="查看原始返回数据（JSON）" name="raw">
          <pre class="raw-json">{{ JSON.stringify(result, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- 错误 -->
    <el-alert v-if="error" :title="error" type="error" show-icon style="margin-top:12px" />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, WarningFilled } from '@element-plus/icons-vue'
import axios from 'axios'
import StockSignalDetail from '@/components/StockSignalDetail.vue'

const today = new Date().toISOString().slice(0, 10)
const form = ref({
  code:    '',
  date:    today,
  timeVal: '10:00',
  factors: ['A', 'B', 'C', 'D'],
})

const loading = ref(false)
const result  = ref(null)
const error   = ref('')

async function calculate() {
  const code = form.value.code.trim().padStart(6, '0')
  if (!code || code === '000000') {
    ElMessage.warning('请输入股票代码')
    return
  }
  loading.value = true
  error.value   = ''
  result.value  = null
  try {
    const res = await axios.post('/api/score_single', {
      code,
      date:    form.value.date,
      hhmm:    form.value.timeVal,
      factors: form.value.factors,
    })
    if (!res.data.ok) throw new Error(res.data.error || '计算失败')
    result.value = res.data
  } catch (e) {
    error.value = e.response?.data?.error || e.message
  } finally {
    loading.value = false
  }
}

// ── 显示工具 ─────────────────────────────────────────────────────────────────
function fResult(fk) {
  if (!result.value) return {}
  return result.value[`factor_${fk.toLowerCase()}`] || {}
}
function fName(fk)  {
  return { A: '位置因子', B: '效率因子', C: '量能因子', D: '板块因子' }[fk]
}
function fTagType(fk) {
  return { A: 'primary', B: 'success', C: 'warning', D: 'danger' }[fk]
}
function posLabel(p) {
  return { low: '低位', break: '突破', high: '高位', other: '其他' }[p] || p
}
function posTagType(p) {
  return { low: 'success', break: 'warning', high: 'danger', other: 'info' }[p] || 'info'
}
function scoreClass(v) {
  if (v === null || v === undefined) return 'info'
  const n = Number(v)
  if (n < 0)  return 'danger'
  if (n >= 2) return 'success'
  if (n >= 1) return 'warning'
  return 'info'
}
const totalScoreClass = computed(() => {
  if (!result.value) return ''
  const s = result.value.summary.score
  if (result.value.summary.is_veto) return 'score-veto'
  if (s >= 6) return 'score-high'
  if (s >= 3) return 'score-mid'
  return 'score-low'
})
function scoreDefText(def) {
  if (!def) return ''
  if (def.type === 'fixed') return `固定 ${def.value} 分`
  return `计算公式: ${def.formula}`
}
function vetoLines(veto) {
  const { triggered, ...rest } = veto
  return rest
}
function vetoValClass(v) {
  if (v.includes('✓')) return 'text-up'
  if (v.includes('✗')) return 'text-down'
  return ''
}
function stepValClass(step) {
  const v = step.value
  if (v === '✓' || v === 'good') return 'text-up'
  if (v === '✗' || v === 'bad') return 'text-down'
  if (typeof v === 'number' && v > 0 && (step.unit === '%' || step.name.includes('涨'))) return 'text-up'
  if (typeof v === 'number' && v < 0) return 'text-down'
  return ''
}

// ── 快捷指标 ─────────────────────────────────────────────────────────────────
const metrics = computed(() => {
  if (!result.value) return []
  const s = result.value.summary
  const pc = result.value.pre_calc
  return [
    { label: '当前价格', value: `${s.current_price} 元` },
    { label: '涨跌幅',   value: `${s.today_change > 0 ? '+' : ''}${s.today_change}%`,
      color: s.today_change > 0 ? '#f56c6c' : s.today_change < 0 ? '#67c23a' : '' },
    { label: '实时量比', value: `${s.rt_vol_ratio}x` },
    { label: '虚拟量比', value: `${s.virt_ratio}x` },
    { label: 'MA120',    value: pc.ma120 ?? '-' },
    { label: 'High120',  value: pc.high120 ?? '-' },
  ]
})
</script>

<style scoped>
.single-wrap { padding: 16px; max-width: 1100px; }

.input-card { margin-bottom: 16px; }
.input-card :deep(.el-card__body) { padding: 14px 16px 4px; }
.input-card :deep(.el-form-item)  { margin-bottom: 10px; }

/* 汇总行 */
.summary-row { display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px; }
.stock-info  { display: flex; align-items: center; gap: 8px; }
.stock-code  { font-size: 20px; font-weight: 700; }
.stock-name  { font-size: 16px; color: #303133; }

.total-score-box {
  min-width: 100px; text-align: center; padding: 10px 18px; border-radius: 8px;
  position: relative;
}
.score-high  { background: #fef0f0; color: #f56c6c; border: 1px solid #fbc4c4; }
.score-mid   { background: #fdf6ec; color: #e6a23c; border: 1px solid #f5dab1; }
.score-low   { background: #f4f4f5; color: #909399; border: 1px solid #dcdfe6; }
.score-veto  { background: #330000; color: #ff4949; border: 1px solid #cc0000; }
.total-label { font-size: 11px; opacity: 0.7; }
.total-value { font-size: 32px; font-weight: 700; line-height: 1.1; }
.veto-badge  { font-size: 11px; font-weight: 600; margin-top: 2px; }

/* 指标行 */
.metric-row  { margin-bottom: 12px; }
.metric-card { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px;
  padding: 8px 10px; text-align: center; }
.metric-label { font-size: 11px; color: #909399; }
.metric-value { font-size: 15px; font-weight: 600; margin-top: 2px; }
.signal-detail { margin-bottom: 12px; }

/* 因子卡片 */
.factors-row { margin-bottom: 12px; }
.factors-row .el-col { margin-bottom: 12px; }
.factor-detail-card :deep(.el-card__header) { padding: 10px 14px; }
.factor-detail-card :deep(.el-card__body)   { padding: 12px 14px; max-height: 420px; overflow-y: auto; }
.fcard-header { display: flex; align-items: center; gap: 8px; }
.f-badge  { font-size: 14px; font-weight: 700; padding: 4px 10px; }
.f-name   { flex: 1; font-size: 14px; font-weight: 600; }
.f-score-box { min-width: 54px; text-align: center; padding: 4px 8px; border-radius: 6px; }
.f-score  { font-size: 22px; font-weight: 700; }
.f-score-label { font-size: 11px; }

/* 得分颜色 */
.success :deep(.f-score) { color: #67c23a; }
.warning :deep(.f-score) { color: #e6a23c; }
.danger  :deep(.f-score) { color: #f56c6c; }
.info    :deep(.f-score) { color: #909399; }

/* 命中规则 */
.hit-rule-row { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.hit-label    { font-size: 12px; color: #909399; }
.score-def-text { font-size: 12px; color: #606266; }

/* 计算步骤 */
.mini-divider { margin: 8px 0 6px; }
.mini-divider :deep(.el-divider__text) { font-size: 11px; color: #909399; }
.step-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 3px 0; border-bottom: 1px dashed #f5f5f5; font-size: 12px;
}
.step-name { color: #606266; flex: 1; }
.step-val  { font-weight: 600; color: #303133; margin-left: 8px; }
.veto-row .step-val { font-family: monospace; }

.text-up   { color: #f56c6c; }
.text-down { color: #67c23a; }

.skipped-tip { color: #909399; font-size: 12px; display: flex; align-items: center; gap: 4px; padding: 8px 0; }
.cache-hint  { font-size: 11px; color: #e6a23c; display: flex; align-items: center; gap: 4px; margin-top: 4px; }
.raw-json { font-size: 11px; max-height: 400px; overflow: auto; background: #1e1e1e;
  color: #d4d4d4; padding: 12px; border-radius: 4px; }
</style>
