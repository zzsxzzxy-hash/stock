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
            <div v-if="row.minute_diag" class="minute-diag">
              <div class="diag-head">
                <el-tag size="small" :type="diagStatusType(row.minute_diag.status_text)">
                  {{ row.minute_diag.status_text || '-' }}
                </el-tag>
                <span>当前 {{ row.minute_diag.now || '-' }}</span>
                <span>稳定应到 {{ row.minute_diag.stable_until || '-' }}</span>
                <span>{{ row.minute_diag.latency_note }}</span>
              </div>

              <div class="diag-grid">
                <div class="diag-box">
                  <div class="diag-title">PG 持久化</div>
                  <div class="diag-line">最新 {{ row.minute_diag.pg?.latest_time || '-' }}，{{ row.minute_diag.pg?.latest_count ?? 0 }} 只，延迟 {{ row.minute_diag.pg?.latest_lag || '-' }}</div>
                  <div class="diag-line">基准 {{ row.minute_diag.pg?.baseline_count ?? 0 }} 只/分钟，阈值 {{ row.minute_diag.pg?.threshold_count ?? 0 }}，共 {{ fmtInt(row.minute_diag.pg?.row_count) }} 条</div>
                  <div class="diag-line">稳定缺口 {{ row.minute_diag.pg?.stable_bad_count ?? 0 }} 个</div>
                  <div v-if="row.minute_diag.pg?.bad_examples?.length" class="diag-examples">
                    <el-tag
                      v-for="item in row.minute_diag.pg.bad_examples"
                      :key="`pg-${item.time}`"
                      size="small"
                      type="danger"
                      effect="plain"
                    >
                      {{ item.time }}:{{ item.count }}
                    </el-tag>
                  </div>
                </div>

                <div class="diag-box">
                  <div class="diag-title">Redis 实时缓存</div>
                  <div class="diag-line">最新 {{ row.minute_diag.redis?.latest_time || '-' }}，{{ row.minute_diag.redis?.latest_count ?? 0 }} 只，延迟 {{ row.minute_diag.redis?.latest_lag || '-' }}</div>
                  <div class="diag-line">键 {{ row.minute_diag.redis?.key_count ?? 0 }} 只，Bar {{ fmtInt(row.minute_diag.redis?.bar_count) }} 条，基准 {{ row.minute_diag.redis?.baseline_count ?? 0 }}</div>
                  <div class="diag-line">稳定缺口 {{ row.minute_diag.redis?.stable_bad_count ?? 0 }} 个</div>
                  <div v-if="row.minute_diag.redis?.latest_distribution?.length" class="diag-examples">
                    <el-tag
                      v-for="item in row.minute_diag.redis.latest_distribution"
                      :key="`redis-latest-${item.time}`"
                      size="small"
                      type="info"
                      effect="plain"
                    >
                      最新{{ item.time }}:{{ item.count }}只
                    </el-tag>
                  </div>
                  <div v-if="row.minute_diag.redis?.bad_examples?.length" class="diag-examples">
                    <el-tag
                      v-for="item in row.minute_diag.redis.bad_examples"
                      :key="`redis-bad-${item.time}`"
                      size="small"
                      type="danger"
                      effect="plain"
                    >
                      {{ item.time }}:{{ item.count }}
                    </el-tag>
                  </div>
                </div>
              </div>

              <div v-if="row.minute_diag.fill_task?.running" class="diag-task">
                补全运行中：{{ row.minute_diag.fill_task.progress || 0 }}%，{{ row.minute_diag.fill_task.message || '-' }}
              </div>
              <div v-else-if="row.minute_diag.fill_task?.updated_at" class="diag-task">
                最近补全：{{ row.minute_diag.fill_task.stage || '-' }}，{{ row.minute_diag.fill_task.updated_at }}，{{ row.minute_diag.fill_task.message || '-' }}
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.action"
              :type="row.ok ? 'default' : 'primary'"
              size="small"
              :loading="actionLoading[row.action]"
              @click="doAction(row.action, row.name)"
            >
              {{ actionLabel(row.action) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 操作结果通知 -->
    <div v-if="!data && !loading" class="empty-tip">
      <el-empty description="无法获取系统状态，请检查后端服务是否运行" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh, CircleCheck, CircleClose, Warning,
  Monitor, DataAnalysis, Calendar,
} from '@element-plus/icons-vue'

const data        = ref(null)
const loading     = ref(false)
const autoRefresh = ref(false)
const actionLoading = ref({})
let   timer       = null

// ── 数据获取 ─────────────────────────────────────────────────────────────────
async function fetchHealth() {
  loading.value = true
  try {
    const res = await fetch('/api/system_health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data.value = await res.json()
  } catch (e) {
    data.value = null
    ElMessage.error(`获取状态失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

function toggleAutoRefresh(val) {
  clearInterval(timer)
  if (val) timer = setInterval(fetchHealth, 30000)
}

onMounted(() => fetchHealth())
onUnmounted(() => clearInterval(timer))

// ── 分组 ──────────────────────────────────────────────────────────────────────
const groupedChecks = computed(() => {
  if (!data.value) return {}
  const groups = {}
  for (const c of data.value.checks) {
    if (!groups[c.category]) groups[c.category] = []
    groups[c.category].push(c)
  }
  return groups
})

// ── 状态显示 ─────────────────────────────────────────────────────────────────
function statusType(row) {
  if (row.ok && !row.warn) return 'success'
  if (row.warn || (row.ok && row.warn)) return 'warning'
  return 'danger'
}
function statusLabel(row) {
  if (row.ok && !row.warn) return '正常'
  if (row.warn) return '警告'
  return '异常'
}
function rowClass({ row }) {
  if (!row.ok && !row.warn && row.required) return 'row-error'
  if (row.warn) return 'row-warn'
  return ''
}

// ── 汇总状态 ──────────────────────────────────────────────────────────────────
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

// ── 分组图标 / 错误计数 ────────────────────────────────────────────────────────
function groupIcon(name) {
  if (name === '基础服务') return 'Monitor'
  if (name === '量能监控') return 'DataAnalysis'
  if (name === '每日数据') return 'Calendar'
  return 'List'
}
function groupErrorCount(group) {
  return group.filter(c => !c.ok && !c.warn && c.required).length
}

function fmtInt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0'
}

function diagStatusType(v) {
  if (v === '完整') return 'success'
  if (v === '采集中/有延迟') return 'warning'
  if (v === '补全任务运行中') return 'warning'
  return 'danger'
}

// ── 操作按钮 ─────────────────────────────────────────────────────────────────
const ACTION_LABELS = {
  restart_daemon:  '重启Daemon',
  run_pre_calc:    '触发预计算',
  fill_minute_bars:'补全K线',
  fill_today_minute_bars: '补全今日K线',
  refresh_rank:    '刷新排行',
  reload_sectors:  '重载板块',
  sync_stock_spot: '同步行情',
}
function actionLabel(action) {
  return ACTION_LABELS[action] || '执行'
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
      // 3秒后刷新状态
      setTimeout(fetchHealth, 3000)
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

/* 顶部汇总 */
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

/* 分组 */
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

/* 表格行 */
:deep(.row-error) td { background: #fff0f0 !important; }
:deep(.row-warn)  td { background: #fffbf0 !important; }

.detail-text     { font-size: 12px; color: #606266; word-break: break-all; }
.detail-err      { color: #f56c6c; font-weight: 500; }

.minute-diag {
  margin-top: 8px;
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.diag-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  color: #606266;
  font-size: 12px;
  margin-bottom: 8px;
}
.diag-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.diag-box {
  padding: 8px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}
.diag-title {
  font-size: 12px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 4px;
}
.diag-line {
  color: #606266;
  font-size: 12px;
  line-height: 1.7;
}
.diag-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.diag-task {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
}
</style>
