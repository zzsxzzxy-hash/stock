<template>
  <div class="sync-page">
    <el-alert
      v-if="globalRunning"
      title="同步任务正在执行中，请稍候..."
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom:12px"
    />

    <template v-for="group in taskGroups" :key="group.name">
      <div class="group-title">{{ group.name }}</div>
      <el-row :gutter="12" style="margin-bottom:4px">
        <el-col
          v-for="task in group.tasks"
          :key="task.key"
          :span="12"
          style="margin-bottom:12px"
        >
          <el-card shadow="hover" class="task-card">
            <template #header>
              <div class="card-header">
                <span class="task-name">{{ task.name }}</span>
                <el-tag :type="statusType(task.key)" size="small" effect="plain">
                  {{ statusText(task.key) }}
                </el-tag>
              </div>
            </template>

            <el-form label-width="60px" size="small">
              <el-form-item label="开始">
                <el-date-picker
                  v-model="task.startDate"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="开始日期"
                  style="width:100%"
                />
              </el-form-item>
              <el-form-item label="结束">
                <el-date-picker
                  v-model="task.endDate"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="结束日期"
                  style="width:100%"
                />
              </el-form-item>
            </el-form>

            <el-button
              type="primary"
              size="small"
              :loading="runningTasks.has(task.key)"
              :disabled="globalRunning && !runningTasks.has(task.key)"
              @click="runTask(task)"
              style="width:100%"
            >
              {{ runningTasks.has(task.key) ? '同步中...' : '开始同步' }}
            </el-button>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- 日志面板 -->
    <el-card shadow="never" class="log-card">
      <template #header>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span>实时日志</span>
          <el-button size="small" @click="logs = []">清空</el-button>
        </div>
      </template>
      <div ref="logBox" class="log-box">
        <div
          v-for="(line, i) in logs"
          :key="i"
          :class="['log-line', logClass(line)]"
        >{{ line }}</div>
        <div v-if="!logs.length" class="log-empty">暂无日志</div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { triggerSync } from '@/api'

const today = new Date().toISOString().slice(0, 10)

const tasks = ref([
  // ── 基础行情数据
  { key: 'basic_stock_spot', name: '股票/ETF实时行情',        group: '基础行情数据', startDate: today, endDate: today },
  { key: 'lhb_fund_bonus',   name: '龙虎榜/资金流/分红/涨停', group: '基础行情数据', startDate: today, endDate: today },
  { key: 'after_close',      name: '大宗交易/北向资金',       group: '基础行情数据', startDate: today, endDate: today },
  { key: 'selection',        name: '综合选股数据',            group: '基础行情数据', startDate: today, endDate: today },
  // ── 技术分析数据
  { key: 'indicators',       name: '技术指标（32+项）',       group: '技术分析数据', startDate: today, endDate: today },
  { key: 'kline_pattern',    name: 'K线形态识别（61种）',     group: '技术分析数据', startDate: today, endDate: today },
  // ── 策略选股数据
  { key: 'strategy',         name: '量化策略选股（10种）',    group: '策略选股数据', startDate: today, endDate: today },
  { key: 'backtest',         name: '策略回测（1~60日收益）',  group: '策略选股数据', startDate: today, endDate: today },
  // ── 自有策略
  { key: 'custom_strategy',  name: '自有策略（爆量股票等）',  group: '自有策略',    startDate: today, endDate: today },
])

// 按 group 分组
const taskGroups = computed(() => {
  const map = {}
  for (const t of tasks.value) {
    if (!map[t.group]) map[t.group] = { name: t.group, tasks: [] }
    map[t.group].tasks.push(t)
  }
  return Object.values(map)
})

const runningTasks = ref(new Set())
const taskStatus   = ref({})
const logs         = ref([])
const logBox       = ref(null)

const globalRunning = computed(() => runningTasks.value.size > 0)

function statusType(key) {
  const s = taskStatus.value[key]
  if (!s) return 'info'
  if (s === 'running') return 'warning'
  if (s === 'success') return 'success'
  if (s === 'failed')  return 'danger'
  return 'info'
}

function statusText(key) {
  const s = taskStatus.value[key]
  if (!s) return '待执行'
  if (s === 'running') return '执行中'
  if (s === 'success') return '成功'
  if (s === 'failed')  return '失败'
  return '待执行'
}

function logClass(line) {
  if (line.includes('❌') || line.includes('失败') || line.includes('ERROR')) return 'log-error'
  if (line.includes('✔') || line.includes('✅') || line.includes('成功') || line.includes('完成')) return 'log-success'
  if (line.includes('⚠') || line.includes('警告') || line.includes('跳过')) return 'log-warn'
  return ''
}

async function scrollLog() {
  await nextTick()
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
}

function appendLog(line) {
  logs.value.push(line)
  scrollLog()
}

async function runTask(task) {
  if (!task.startDate || !task.endDate) {
    ElMessage.warning('请选择开始和结束日期')
    return
  }

  runningTasks.value = new Set([...runningTasks.value, task.key])
  taskStatus.value[task.key] = 'running'
  appendLog(`\n▶ 开始同步：${task.name}  [${task.startDate} ~ ${task.endDate}]`)

  try {
    const res = await triggerSync(task.key, task.startDate, task.endDate)
    const taskKey = res.data.task_key

    await new Promise((resolve) => {
      const es = new EventSource(`/instock/api/sync/log?task_key=${taskKey}`)
      es.onmessage = (e) => {
        if (e.data === '__DONE__') { es.close(); resolve(); return }
        appendLog(e.data)
      }
      es.onerror = () => { es.close(); resolve() }
      setTimeout(() => { es.close(); resolve() }, 300_000)
    })

    taskStatus.value[task.key] = 'success'
    appendLog(`✅ ${task.name} 同步完成`)
    ElMessage.success(`${task.name} 同步完成`)
  } catch (e) {
    taskStatus.value[task.key] = 'failed'
    appendLog(`❌ ${task.name} 同步失败：${e.message}`)
    ElMessage.error(`${task.name} 同步失败`)
  } finally {
    const s = new Set(runningTasks.value)
    s.delete(task.key)
    runningTasks.value = s
  }
}
</script>

<style scoped>
.sync-page { display: flex; flex-direction: column; gap: 0; }

.group-title {
  font-size: 13px;
  font-weight: 700;
  color: #303133;
  border-left: 3px solid #409eff;
  padding-left: 8px;
  margin: 8px 0 10px;
}

.card-header { display: flex; align-items: center; justify-content: space-between; }
.task-name { font-weight: 600; }
.task-card :deep(.el-card__body) { padding: 12px; }

.log-card { margin-top: 8px; }
.log-card :deep(.el-card__body) { padding: 0; }

.log-box {
  height: 300px;
  overflow-y: auto;
  background: #1e1e1e;
  padding: 10px 14px;
  font-family: 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
}
.log-line    { color: #d4d4d4; white-space: pre-wrap; word-break: break-all; }
.log-error   { color: #f48771; }
.log-success { color: #4ec9b0; }
.log-warn    { color: #dcdcaa; }
.log-empty   { color: #555; font-style: italic; }
</style>
