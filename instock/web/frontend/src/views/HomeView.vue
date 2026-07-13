<template>
  <div class="health-wrap">
    <!-- 顶部汇总卡片 -->
    <div class="summary-bar">
      <div class="summary-card" :class="summaryClass">
        <el-icon size="24"><component :is="summaryIcon" /></el-icon>
        <div class="summary-text">
          <div class="summary-title">{{ summaryTitle }}</div>
          <div class="summary-sub">{{ data ? `今日 ${data.today}  当前 ${data.hhmm}  ${data.is_trade_day ? '✓ 交易日' : '✗ 非交易日'}` : '加载中…' }}</div>
        </div>
        <div class="summary-right">
          <el-tag v-if="data && data.error_count > 0" type="danger" effect="dark">
            {{ data.error_count }} 项异常
          </el-tag>
          <el-tag v-if="data && data.warn_count > 0" type="warning" effect="dark" style="margin-left:6px">
            {{ data.warn_count }} 项警告
          </el-tag>
          <el-button
            :icon="Refresh"
            :loading="loading"
            size="small"
            @click="fetchHealth"
            style="margin-left: 12px"
          >刷新</el-button>
          <el-switch
            v-model="autoRefresh"
            active-text="自动刷新(30s)"
            size="small"
            style="margin-left: 12px"
            @change="toggleAutoRefresh"
          />
        </div>
      </div>
    </div>

    <!-- 分组表格 -->
    <div v-if="data" v-for="(group, gname) in groupedChecks" :key="gname" class="group-block">
      <div class="group-header">
        <el-icon><component :is="groupIcon(gname)" /></el-icon>
        <span>{{ gname }}</span>
        <el-tag
          v-if="groupErrorCount(group) > 0"
          type="danger"
          size="small"
          effect="plain"
          style="margin-left:8px"
        >{{ groupErrorCount(group) }} 异常</el-tag>
      </div>

      <el-table
        :data="group"
        border
        size="small"
        :row-class-name="rowClass"
        style="width: 100%"
      >
        <el-table-column label="检查项" prop="name" min-width="190" />

        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag
              :type="statusType(row)"
              effect="light"
              size="small"
            >{{ statusLabel(row) }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="必要" width="60" align="center">
          <template #default="{ row }">
            <el-text :type="row.required ? 'danger' : 'info'" size="small">
              {{ row.required ? '必须' : '可选' }}
            </el-text>
          </template>
        </el-table-column>

        <el-table-column label="详情" min-width="260">
          <template #default="{ row }">
            <span class="detail-text" :class="{ 'detail-err': !row.ok && !row.warn }">
              {{ row.detail || '-' }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.action"
              :type="row.ok ? 'default' : 'primary'"
              size="small"
              :loading="isActionLoading(row.action)"
              :disabled="isActionDisabled(row.action)"
              @click="doAction(row.action, row.name)"
            >
              {{ actionButtonLabel(row.action) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="gname === '每日数据' && minuteFillPanelVisible" class="task-panel">
        <div class="task-panel-head">
          <div class="task-title">
            <span>补全今日K线进度</span>
            <el-tag :type="minuteFillTagType" size="small" effect="light">
              {{ minuteFillStatusLabel }}
            </el-tag>
          </div>
          <div class="task-message">{{ minuteFillTask?.message || '-' }}</div>
        </div>
        <el-progress
          :percentage="minuteFillProgress"
          :status="minuteFillProgressStatus"
          :stroke-width="8"
        />
        <div class="task-meta">
          <span>开始 {{ minuteFillTask?.started_at || '-' }}</span>
          <span>更新 {{ minuteFillTask?.updated_at || '-' }}</span>
          <span>阶段 {{ minuteFillTask?.stage || '-' }}</span>
        </div>
        <div ref="minuteFillLogEl" class="task-log">
          <div
            v-for="item in minuteFillLogs"
            :key="item.seq"
            class="task-log-line"
            :class="`log-${item.level || 'info'}`"
          >
            <span class="log-time">{{ item.time }}</span>
            <span class="log-progress">{{ item.progress }}%</span>
            <span class="log-message">{{ item.message }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 加载失败 -->
    <div v-if="!data && !loading" class="empty-tip">
      <el-empty description="无法获取系统状态，请检查后端服务是否运行" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh, CircleCheck, CircleClose, Warning,
  Monitor, DataAnalysis, Calendar,
} from '@element-plus/icons-vue'

const data          = ref(null)
const loading       = ref(false)
const autoRefresh   = ref(false)
const actionLoading = ref({})
const minuteFillTask = ref(null)
const minuteFillLogEl = ref(null)
let   timer         = null
let   minuteFillTimer = null
let   lastSilentHealthAt = 0

async function fetchHealth(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await fetch('/api/system_health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data.value = await res.json()
  } catch (e) {
    if (!silent) data.value = null
    if (!silent) ElMessage.error(`获取状态失败: ${e.message}`)
  } finally {
    if (!silent) loading.value = false
  }
}

function toggleAutoRefresh(val) {
  clearInterval(timer)
  if (val) timer = setInterval(fetchHealth, 30000)
}

onMounted(() => {
  fetchHealth()
  fetchMinuteFillStatus(true)
})
onUnmounted(() => {
  clearInterval(timer)
  stopMinuteFillPolling()
})

const groupedChecks = computed(() => {
  if (!data.value) return {}
  const groups = {}
  for (const c of data.value.checks) {
    if (!groups[c.category]) groups[c.category] = []
    groups[c.category].push(c)
  }
  return groups
})

function statusType(row) {
  if (row.running) return 'primary'
  if (row.ok && !row.warn) return 'success'
  if (row.warn) return 'warning'
  return 'danger'
}
function statusLabel(row) {
  if (row.running) return '运行中'
  if (row.ok && !row.warn) return '正常'
  if (row.warn) return '警告'
  return '异常'
}
function rowClass({ row }) {
  if (row.running) return 'row-running'
  if (!row.ok && !row.warn && row.required) return 'row-error'
  if (row.warn) return 'row-warn'
  return ''
}

const summaryClass = computed(() => {
  if (!data.value) return 'summary-loading'
  if (data.value.error_count > 0) return 'summary-error'
  if (data.value.warn_count > 0) return 'summary-warn'
  return 'summary-ok'
})
const summaryIcon = computed(() => {
  if (!data.value) return 'Loading'
  if (data.value.error_count > 0) return 'CircleClose'
  if (data.value.warn_count > 0) return 'Warning'
  return 'CircleCheck'
})
const summaryTitle = computed(() => {
  if (!data.value) return '检查中…'
  if (data.value.error_count > 0) return `系统异常 — ${data.value.error_count} 项必要服务未正常运行`
  if (data.value.warn_count > 0) return '系统基本正常（有警告项）'
  return '所有服务正常运行'
})

function groupIcon(name) {
  if (name === '基础服务') return 'Monitor'
  if (name === '量能监控') return 'DataAnalysis'
  if (name === '每日数据') return 'Calendar'
  return 'List'
}
function groupErrorCount(group) {
  return group.filter(c => !c.ok && !c.warn && c.required).length
}

const ACTION_LABELS = {
  restart_daemon:    '重启Daemon',
  run_pre_calc:      '触发预计算',
  fill_minute_bars:  '补全K线',
  fill_today_minute_bars: '补全今日K线',
  refresh_rank:      '刷新排行',
  reload_sectors:    '同步板块数据',
  sync_stock_spot:   '同步行情',
  sync_hist_data:    '同步基本数据',
  sync_volume_surge: '同步爆量数据',
}
function actionLabel(action) {
  return ACTION_LABELS[action] || '执行'
}
function actionButtonLabel(action) {
  if (action === 'fill_today_minute_bars' && minuteFillRunning.value) return '补全中'
  return actionLabel(action)
}
function isActionLoading(action) {
  return !!actionLoading.value[action] || (action === 'fill_today_minute_bars' && minuteFillRunning.value)
}
function isActionDisabled(action) {
  return action === 'fill_today_minute_bars' && minuteFillRunning.value
}

const minuteFillRunning = computed(() => !!minuteFillTask.value?.running)
const minuteFillLogs = computed(() => minuteFillTask.value?.logs || [])
const minuteFillPanelVisible = computed(() => {
  const task = minuteFillTask.value
  return !!(task && (task.running || (task.logs && task.logs.length > 0)))
})
const minuteFillProgress = computed(() => {
  const n = Number(minuteFillTask.value?.progress || 0)
  return Math.max(0, Math.min(100, Math.round(n)))
})
const minuteFillStatusLabel = computed(() => {
  const task = minuteFillTask.value
  if (!task) return '未运行'
  if (task.running) return '运行中'
  if (task.stage === 'incomplete') return '仍不完整'
  if (task.ok === true) return '已完成'
  if (task.ok === false) return '失败'
  return '未运行'
})
const minuteFillTagType = computed(() => {
  const task = minuteFillTask.value
  if (!task) return 'info'
  if (task.running) return 'primary'
  if (task.stage === 'incomplete') return 'warning'
  if (task.ok === true) return 'success'
  if (task.ok === false) return 'danger'
  return 'info'
})
const minuteFillProgressStatus = computed(() => {
  if (minuteFillTask.value?.stage === 'incomplete') return 'warning'
  if (minuteFillTask.value?.ok === true) return 'success'
  if (minuteFillTask.value?.ok === false) return 'exception'
  return ''
})

function stopMinuteFillPolling() {
  clearInterval(minuteFillTimer)
  minuteFillTimer = null
}

function startMinuteFillPolling() {
  if (minuteFillTimer) return
  minuteFillTimer = setInterval(() => fetchMinuteFillStatus(true), 1500)
}

function scrollMinuteFillLog() {
  nextTick(() => {
    const el = minuteFillLogEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function fetchMinuteFillStatus(silent = false) {
  try {
    const res = await fetch('/api/system_action_status?action=fill_today_minute_bars')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = await res.json()
    if (json.ok && json.task) {
      minuteFillTask.value = json.task
      scrollMinuteFillLog()
      if (json.task.running) {
        startMinuteFillPolling()
        const now = Date.now()
        if (now - lastSilentHealthAt > 5000) {
          lastSilentHealthAt = now
          fetchHealth(true)
        }
      } else {
        stopMinuteFillPolling()
        fetchHealth(true)
      }
    }
  } catch (e) {
    if (!silent) ElMessage.error(`获取补全进度失败: ${e.message}`)
  }
}

async function doAction(action, name) {
  try {
    await ElMessageBox.confirm(
      `确认执行「${actionLabel(action)}」操作？`,
      name,
      { confirmButtonText: '执行', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  actionLoading.value[action] = true
  try {
    const res = await fetch('/api/system_action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    const json = await res.json()
    if (json.ok) {
      ElMessage.success(json.msg || '操作成功')
      if (action === 'fill_today_minute_bars') {
        if (json.task) minuteFillTask.value = json.task
        scrollMinuteFillLog()
        startMinuteFillPolling()
        fetchHealth(true)
      }
      setTimeout(() => fetchHealth(), 3000)
    } else {
      ElMessage.error(json.msg || '操作失败')
    }
  } catch (e) {
    ElMessage.error(`请求失败: ${e.message}`)
  } finally {
    actionLoading.value[action] = false
  }
}
</script>

<style scoped>
.health-wrap {
  padding: 16px;
  max-width: 1100px;
}

.summary-bar { margin-bottom: 18px; }
.summary-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}
.summary-ok      { background: #f0f9eb; border-color: #b3e19d; color: #529b2e; }
.summary-error   { background: #fef0f0; border-color: #fbc4c4; color: #c45656; }
.summary-warn    { background: #fdf6ec; border-color: #f5dab1; color: #b88230; }
.summary-loading { background: #f4f4f5; border-color: #e9e9eb; color: #909399; }

.summary-text { flex: 1; }
.summary-title { font-size: 15px; font-weight: 600; }
.summary-sub   { font-size: 12px; opacity: 0.8; margin-top: 2px; }
.summary-right { display: flex; align-items: center; flex-shrink: 0; }

.group-block { margin-bottom: 20px; }
.group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  padding: 0 0 8px 2px;
  border-bottom: 2px solid #409eff30;
  margin-bottom: 8px;
}

:deep(.row-error) td { background: #fff0f0 !important; }
:deep(.row-warn)  td { background: #fffbf0 !important; }
:deep(.row-running) td { background: #ecf5ff !important; }

.detail-text { font-size: 12px; color: #606266; word-break: break-all; }
.detail-err  { color: #f56c6c; font-weight: 500; }

.task-panel {
  border: 1px solid #dcdfe6;
  border-top: 0;
  padding: 12px 14px 14px;
  background: #fbfdff;
}
.task-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}
.task-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
}
.task-message {
  flex: 1;
  font-size: 12px;
  line-height: 1.5;
  color: #606266;
  text-align: right;
  word-break: break-all;
}
.task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin: 8px 0;
  font-size: 12px;
  color: #909399;
}
.task-log {
  height: 180px;
  overflow: auto;
  border: 1px solid #ebeef5;
  background: #ffffff;
  padding: 6px 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
}
.task-log-line {
  display: grid;
  grid-template-columns: 64px 44px minmax(0, 1fr);
  column-gap: 8px;
  color: #606266;
  border-bottom: 1px solid #f5f7fa;
}
.task-log-line:last-child { border-bottom: 0; }
.log-time { color: #909399; }
.log-progress { color: #409eff; text-align: right; }
.log-message { word-break: break-all; }
.log-warning .log-message { color: #b88230; }
.log-error .log-message { color: #f56c6c; }
</style>
