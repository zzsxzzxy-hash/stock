<template>
  <div class="journal-wrap">
    <div class="page-header">
      <div>
        <div class="page-title">每日操作记录</div>
        <div class="page-sub">持仓、卖出、换股都按同一张台账复盘</div>
      </div>
      <div class="header-actions">
        <el-date-picker
          v-model="filters.date"
          type="date"
          value-format="YYYY-MM-DD"
          style="width: 145px"
          clearable
          placeholder="交易日期"
          @change="loadLogs"
        />
        <el-input
          v-model="filters.code"
          style="width: 120px"
          maxlength="6"
          clearable
          placeholder="代码"
          @keyup.enter="loadLogs"
        />
        <el-button :icon="Refresh" :loading="loading" @click="loadLogs">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openDialog()">新增记录</el-button>
      </div>
    </div>

    <el-row :gutter="12" class="metric-row">
      <el-col :span="8">
        <div class="metric-card">
          <div class="metric-label">记录数</div>
          <div class="metric-value">{{ rows.length }}</div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="metric-card">
          <div class="metric-label">买入</div>
          <div class="metric-value">{{ actionCount('buy') }}</div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="metric-card">
          <div class="metric-label">卖出/换股</div>
          <div class="metric-value">{{ actionCount('sell') + actionCount('switch') }}</div>
        </div>
      </el-col>
    </el-row>

    <el-card shadow="never" class="table-card">
      <el-table v-loading="loading" :data="rows" border size="small" row-key="id">
        <el-table-column prop="trade_date" label="日期" width="108" fixed />
        <el-table-column prop="trade_time" label="时间" width="76" />
        <el-table-column prop="code" label="代码" width="86" />
        <el-table-column prop="name" label="名称" width="110" />
        <el-table-column prop="action" label="动作" width="96" align="center">
          <template #default="{ row }">
            <el-tag :type="actionType(row.action)" size="small">{{ actionLabel(row.action) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格" width="86" align="right">
          <template #default="{ row }">{{ fmtNum(row.price, 2) }}</template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.quantity, 0) }}</template>
        </el-table-column>
        <el-table-column prop="mainline" label="主线" min-width="120" />
        <el-table-column prop="result" label="结果" width="104" />
        <el-table-column prop="pnl_pct" label="盈亏" width="92" align="right">
          <template #default="{ row }">
            <span v-if="row.pnl_pct !== null && row.pnl_pct !== undefined" :class="pnlClass(row.pnl_pct)">
              {{ fmtPnl(row.pnl_pct) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="system_judgment" label="系统判断" min-width="360" show-overflow-tooltip>
          <template #default="{ row }">{{ row.system_judgment || '-' }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="操作理由" min-width="220" show-overflow-tooltip />
        <el-table-column prop="follow_plan" label="次日计划" min-width="200" show-overflow-tooltip />
        <el-table-column label="操作" width="144" fixed="right" align="center">
          <template #default="{ row }">
            <el-button size="small" :icon="EditPen" @click="openDialog(row)">编辑</el-button>
            <el-button size="small" type="danger" :icon="Delete" @click="removeLog(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑操作记录' : '新增操作记录'"
      width="760px"
      destroy-on-close
    >
      <el-form :model="form" label-width="92px" class="log-form">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="日期">
              <el-date-picker v-model="form.trade_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="时间">
              <el-time-picker v-model="form.trade_time" format="HH:mm" value-format="HH:mm" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="动作">
              <el-select v-model="form.action" style="width: 100%">
                <el-option label="买入" value="buy" />
                <el-option label="卖出" value="sell" />
                <el-option label="持有复审" value="hold" />
                <el-option label="换股" value="switch" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="代码">
              <el-input v-model="form.code" maxlength="6" placeholder="688585" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="名称">
              <el-input v-model="form.name" placeholder="股票名称" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="主线">
              <el-input v-model="form.mainline" placeholder="机器人/半导体" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="价格">
              <el-input-number v-model="form.price" :precision="3" :step="0.01" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="数量">
              <el-input-number v-model="form.quantity" :precision="0" :step="100" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="结果">
              <el-select v-model="form.result" style="width: 100%" clearable>
                <el-option label="待复盘" value="待复盘" />
                <el-option label="盈利" value="盈利" />
                <el-option label="亏损" value="亏损" />
                <el-option label="错误买入" value="错误买入" />
                <el-option label="执行正确" value="执行正确" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="系统判断">
          <div class="system-judgment-field">
            <el-input
              v-model="form.system_judgment"
              type="textarea"
              :rows="2"
              readonly
              placeholder="填写日期、时间和代码后自动识别"
            />
            <el-tooltip content="按当前日期、时间和代码重新识别" placement="top">
              <el-button
                :icon="Refresh"
                circle
                :loading="signalLoading"
                @click="loadSystemJudgment(true)"
              />
            </el-tooltip>
          </div>
        </el-form-item>

        <el-form-item label="操作理由">
          <el-input
            v-model="form.reason"
            type="textarea"
            :rows="3"
            placeholder="主线判断、个股地位、买点状态、卖出原因"
          />
        </el-form-item>
        <el-form-item label="次日计划">
          <el-input
            v-model="form.follow_plan"
            type="textarea"
            :rows="2"
            placeholder="低开处理、冲高处理、换股条件"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveLog">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, EditPen, Plus, Refresh } from '@element-plus/icons-vue'
import axios from 'axios'

const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const signalLoading = ref(false)
const rows = ref([])
const dialogVisible = ref(false)
const lastSignalKey = ref('')
let signalLookupTimer = null
const filters = ref({
  date: '',
  code: '',
})

const emptyForm = () => ({
  id: null,
  trade_date: todayStr(),
  trade_time: '09:45',
  code: '',
  name: '',
  action: 'buy',
  price: null,
  quantity: null,
  mainline: '',
  strategy: '超短主线接力',
  reason: '',
  result: '待复盘',
  follow_plan: '',
  system_judgment: '',
  signal_strategy: '',
  signal_snapshot_time: '',
  signal_core_score: null,
  signal_mode: '',
  signal_buy_status: '',
  signal_amount_ratio: null,
  signal_risk: '',
})
const form = ref(emptyForm())

function todayStr() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function loadLogs() {
  loading.value = true
  try {
    const params = {}
    if (filters.value.date) params.date = filters.value.date
    if (filters.value.code) params.code = filters.value.code
    const res = await axios.get('/api/operation_log', { params })
    rows.value = res.data.data || []
  } catch (e) {
    ElMessage.error('操作记录加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

function openDialog(row = null) {
  if (row) {
    form.value = {
      ...emptyForm(),
      ...row,
      price: row.price === null || row.price === undefined ? null : Number(row.price),
      quantity: row.quantity === null || row.quantity === undefined ? null : Number(row.quantity),
      signal_core_score: toNumberOrNull(row.signal_core_score),
      signal_amount_ratio: toNumberOrNull(row.signal_amount_ratio),
    }
    lastSignalKey.value = currentSignalKey()
  } else {
    form.value = emptyForm()
    applyQueryToForm()
    if (form.value.system_judgment) {
      lastSignalKey.value = currentSignalKey()
    } else {
      scheduleSystemJudgment()
    }
  }
  dialogVisible.value = true
}

function applyQueryToForm() {
  const q = route.query || {}
  if (q.date) form.value.trade_date = String(q.date)
  if (q.time) form.value.trade_time = String(q.time)
  if (q.code) form.value.code = String(q.code).padStart(6, '0')
  if (q.name) form.value.name = String(q.name)
  if (q.mainline) form.value.mainline = String(q.mainline)
  if (q.action) form.value.action = String(q.action)
  if (q.system_judgment) form.value.system_judgment = String(q.system_judgment)
  if (q.signal_strategy) form.value.signal_strategy = String(q.signal_strategy)
  if (q.signal_snapshot_time) form.value.signal_snapshot_time = String(q.signal_snapshot_time)
  if (q.signal_core_score !== undefined) form.value.signal_core_score = toNumberOrNull(q.signal_core_score)
  if (q.signal_mode) form.value.signal_mode = String(q.signal_mode)
  if (q.signal_buy_status) form.value.signal_buy_status = String(q.signal_buy_status)
  if (q.signal_amount_ratio !== undefined) form.value.signal_amount_ratio = toNumberOrNull(q.signal_amount_ratio)
  if (q.signal_risk) form.value.signal_risk = String(q.signal_risk)
}

function currentSignalKey() {
  const rawCode = String(form.value.code || '').trim()
  if (!form.value.trade_date || !form.value.trade_time || !/^\d{6}$/.test(rawCode)) return ''
  const code = rawCode.padStart(6, '0')
  return `${form.value.trade_date}|${form.value.trade_time}|${code}`
}

function toNumberOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function signalNumber(value, digits) {
  const n = toNumberOrNull(value)
  return n === null ? '-' : n.toFixed(digits)
}

function buildSystemJudgment() {
  return [
    `核心分：${signalNumber(form.value.signal_core_score, 1)}`,
    `模式：${form.value.signal_mode || '-'}`,
    `买入状态：${form.value.signal_buy_status || '-'}`,
    `量比：${signalNumber(form.value.signal_amount_ratio, 2)}`,
    `风险：${form.value.signal_risk || '无'}`,
  ].join('；')
}

function applySignalDetail(detail, snapshot) {
  let risks = Array.isArray(detail.risk_tags)
    ? detail.risk_tags.join(' / ')
    : String(detail.risk_tags || '')
  if (!risks) {
    risks = String(detail.tags || '').split(',').filter(Boolean).slice(0, 3).join(' / ')
  }
  form.value.signal_strategy = 'mainline_core'
  form.value.signal_snapshot_time = snapshot
  form.value.signal_core_score = toNumberOrNull(detail.core_score ?? detail.score)
  form.value.signal_mode = detail.trade_mode || detail.signal_type || ''
  form.value.signal_buy_status = detail.observe_label || ''
  form.value.signal_amount_ratio = toNumberOrNull(detail.amt_vs_prev)
  form.value.signal_risk = risks
  form.value.system_judgment = buildSystemJudgment()
  if (!form.value.name) form.value.name = detail.name || ''
  if (!form.value.mainline) {
    form.value.mainline = detail.mainline_theme || detail.trade_theme || detail.best_sector || ''
  }
}

async function loadSystemJudgment(showError = false) {
  const key = currentSignalKey()
  if (!key) {
    if (showError) ElMessage.warning('请先填写完整的日期、时间和6位股票代码')
    return false
  }
  signalLoading.value = true
  try {
    const code = String(form.value.code || '').trim().padStart(6, '0')
    const snapshot = form.value.trade_time
    const res = await axios.get('/api/stock_signal_detail', {
      params: { code, date: form.value.trade_date, snapshot },
      timeout: 90000,
    })
    if (key !== currentSignalKey()) return false
    applySignalDetail(res.data.data || {}, res.data.snapshot || snapshot)
    lastSignalKey.value = key
    return true
  } catch (e) {
    if (showError) {
      ElMessage.error('系统判断识别失败：' + (e.response?.data?.error || e.message))
    }
    return false
  } finally {
    signalLoading.value = false
  }
}

function scheduleSystemJudgment() {
  if (signalLookupTimer) clearTimeout(signalLookupTimer)
  const key = currentSignalKey()
  if (!dialogVisible.value || form.value.id || !key || key === lastSignalKey.value) return
  signalLookupTimer = setTimeout(() => loadSystemJudgment(false), 450)
}

async function saveLog() {
  if (!form.value.trade_date || !form.value.code || !form.value.action) {
    ElMessage.warning('日期、代码、动作必填')
    return
  }
  saving.value = true
  try {
    if (!form.value.system_judgment) await loadSystemJudgment(false)
    const payload = {
      ...form.value,
      code: String(form.value.code || '').trim().padStart(6, '0'),
    }
    if (payload.id) {
      await axios.put('/api/operation_log', payload)
      ElMessage.success('记录已更新')
    } else {
      await axios.post('/api/operation_log', payload)
      ElMessage.success('记录已保存')
    }
    dialogVisible.value = false
    await loadLogs()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.error || e.message))
  } finally {
    saving.value = false
  }
}

async function removeLog(row) {
  try {
    await ElMessageBox.confirm(`删除 ${row.trade_date} ${row.code} 的记录？`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await axios.delete('/api/operation_log', { params: { id: row.id } })
    ElMessage.success('记录已删除')
    await loadLogs()
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.error || e.message))
  }
}

function actionCount(action) {
  return rows.value.filter(row => row.action === action).length
}

function actionLabel(action) {
  return {
    buy: '买入',
    sell: '卖出',
    hold: '持有复审',
    switch: '换股',
  }[action] || action || '-'
}

function actionType(action) {
  return {
    buy: 'danger',
    sell: 'success',
    hold: 'warning',
    switch: 'primary',
  }[action] || 'info'
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

function fmtPnl(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function pnlClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'pnl-profit' : 'pnl-loss'
}

watch(
  () => route.query,
  () => {
    if (route.query?.code) {
      openDialog()
    }
  },
  { immediate: false }
)

watch(
  () => [dialogVisible.value, form.value.trade_date, form.value.trade_time, form.value.code],
  scheduleSystemJudgment
)

onMounted(async () => {
  if (route.query?.date) filters.value.date = String(route.query.date)
  if (route.query?.code) {
    filters.value.code = String(route.query.code).padStart(6, '0')
    openDialog()
  }
  await loadLogs()
})
</script>

<style scoped>
.journal-wrap { padding: 16px; }
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.page-title { font-size: 18px; font-weight: 700; color: #303133; }
.page-sub { margin-top: 4px; font-size: 12px; color: #909399; }
.header-actions { display: flex; align-items: center; gap: 8px; }
.metric-row { margin-bottom: 12px; }
.metric-card {
  height: 74px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.metric-card.danger { border-color: #f3d0d0; }
.metric-label { font-size: 12px; color: #909399; }
.metric-value { margin-top: 8px; font-size: 22px; font-weight: 700; color: #303133; }
.metric-card.danger .metric-value { color: #c45656; }
.pnl-profit { color: #f56c6c; font-weight: 600; }
.pnl-loss { color: #67c23a; font-weight: 600; }
.system-judgment-field {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
}
.system-judgment-field .el-textarea { flex: 1; }
.table-card :deep(.el-card__body) { padding: 0; }
.log-form :deep(.el-form-item) { margin-bottom: 14px; }

@media (max-width: 1180px) {
  .page-header { flex-direction: column; }
  .header-actions { flex-wrap: wrap; }
}
</style>
