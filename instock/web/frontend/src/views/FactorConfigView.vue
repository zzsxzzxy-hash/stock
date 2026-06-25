<template>
  <div class="factor-wrap">
    <div class="page-header">
      <div>
        <div class="page-title">量能因子配置</div>
        <div class="page-sub">修改后立即生效于量能异动监控的实时计算</div>
      </div>
      <div class="header-btns">
        <el-button :loading="saving" type="primary" :icon="Check" @click="saveAll">保存配置</el-button>
        <el-button :icon="RefreshLeft" @click="resetAll">恢复默认</el-button>
      </div>
    </div>

    <div v-if="config" class="factors">
      <el-card v-for="fk in factorKeys" :key="fk" class="factor-card" shadow="hover">
        <!-- 因子标题 -->
        <template #header>
          <div class="factor-header">
            <el-tag :type="tagType(fk)" effect="dark" size="large" class="factor-tag">{{ fk }}</el-tag>
            <div class="factor-title-block">
              <span class="factor-name">{{ config[fk].name }}</span>
              <span class="factor-desc">{{ config[fk].desc }}</span>
            </div>
            <el-switch v-model="config[fk].enabled" active-text="启用" inactive-text="禁用" />
          </div>
        </template>

        <div :class="{ 'disabled-mask': !config[fk].enabled }">
          <!-- 计算公式说明 -->
          <el-alert :title="config[fk].formula" type="info" :closable="false" show-icon
                    class="formula-alert" />

          <!-- 阈值参数 -->
          <template v-if="config[fk].thresholds">
            <div class="section-title">
              <el-icon><Setting /></el-icon> 计算参数
            </div>
            <el-row :gutter="16">
              <el-col
                v-for="(th, tk) in config[fk].thresholds"
                :key="tk" :span="12" style="margin-bottom:10px"
              >
                <div class="th-row">
                  <span class="th-label">{{ th.label }}</span>
                  <el-input-number
                    v-model="th.value"
                    :precision="2" :step="0.05" size="small"
                    style="width:110px"
                  />
                </div>
              </el-col>
            </el-row>
          </template>

          <!-- 一票否决参数（因子A专属） -->
          <template v-if="config[fk].veto">
            <div class="section-title veto-title">
              <el-icon><Warning /></el-icon> 一票否决条件
              <el-switch v-model="config[fk].veto.enabled" size="small" style="margin-left:8px" />
            </div>
            <el-row :gutter="16">
              <el-col
                v-for="(th, tk) in vetoThresholds(fk)"
                :key="tk" :span="12" style="margin-bottom:10px"
              >
                <div class="th-row">
                  <span class="th-label">{{ th.label }}</span>
                  <el-input-number
                    v-model="th.value"
                    :precision="2" :step="0.05" size="small"
                    style="width:110px"
                  />
                </div>
              </el-col>
            </el-row>
          </template>

          <!-- 得分配置 -->
          <div class="section-title">
            <el-icon><Tickets /></el-icon> 得分规则
          </div>
          <el-table :data="scoresTableData(fk)" border size="small" class="scores-table">
            <el-table-column label="条件" prop="label" min-width="260">
              <template #default="{ row }">
                <div class="score-label">
                  <el-tag size="small" :type="scoreTagType(row.key)" effect="plain">{{ row.key }}</el-tag>
                  <span style="margin-left:6px; font-size:12px; color:#606266">{{ row.label }}</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="计分方式" width="130" align="center">
              <template #default="{ row }">
                <el-select v-model="row.item.type" size="small" style="width:110px"
                           @change="onTypeChange(fk, row.key, $event)">
                  <el-option label="固定分数" value="fixed" />
                  <el-option label="计算分数" value="calc" />
                </el-select>
              </template>
            </el-table-column>

            <el-table-column label="固定分值 / 计算公式" min-width="220">
              <template #default="{ row }">
                <el-input-number
                  v-if="row.item.type === 'fixed'"
                  v-model="row.item.value"
                  :precision="1" :step="0.5"
                  size="small" style="width:100px"
                />
                <div v-else class="formula-input-wrap">
                  <el-input
                    v-model="row.item.formula"
                    size="small"
                    placeholder="如: e_today / e_y"
                    style="width:100%"
                  />
                  <div class="formula-hint">
                    可用变量：{{ formulaVars(fk) }}
                  </div>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="预览得分" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="scoreValueType(row.item.type === 'fixed' ? row.item.value : null)"
                        size="small" effect="plain">
                  {{ row.item.type === 'fixed' ? row.item.value : '动态' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </div>

    <div v-else class="loading-tip">
      <el-skeleton :rows="8" animated />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, RefreshLeft, Setting, Warning, Tickets } from '@element-plus/icons-vue'
import axios from 'axios'

const config  = ref(null)
const saving  = ref(false)
const factorKeys = ['A', 'B', 'C', 'D']

// ── 变量提示 ────────────────────────────────────────────────────────────────
const FORMULA_VARS = {
  A: 'close, ma120, high120',
  B: 'e_today, e_y, e_prev',
  C: 'vol_ratio, avg_up, avg_down',
  D: 'signal_count, avg_change',
}
function formulaVars(fk) { return FORMULA_VARS[fk] || '' }

// ── 样式工具 ─────────────────────────────────────────────────────────────────
function tagType(fk) {
  return { A: 'primary', B: 'success', C: 'warning', D: 'danger' }[fk] || 'info'
}
function scoreTagType(key) {
  if (['veto', 'high_bad', 'none'].includes(key)) return 'danger'
  if (['low', 'break', 'high_good', 'strong', 'continuous'].includes(key)) return 'success'
  if (['single', 'normal', 'weak'].includes(key)) return 'warning'
  return 'info'
}
function scoreValueType(val) {
  if (val === null) return 'primary'
  if (val < 0) return 'danger'
  if (val >= 2) return 'success'
  if (val >= 1) return 'warning'
  return 'info'
}

// ── 将 scores 对象转成 table 数组 ─────────────────────────────────────────
function scoresTableData(fk) {
  const scores = config.value?.[fk]?.scores || {}
  return Object.entries(scores).map(([key, item]) => ({ key, label: item.label || key, item }))
}

// ── 过滤掉 veto.enabled 字段，只返回数值阈值 ──────────────────────────────
function vetoThresholds(fk) {
  const veto = config.value?.[fk]?.veto || {}
  return Object.fromEntries(
    Object.entries(veto).filter(([k, v]) => k !== 'enabled' && typeof v === 'object')
  )
}

// ── type 切换时补全缺省值 ─────────────────────────────────────────────────
function onTypeChange(fk, key, newType) {
  const item = config.value[fk].scores[key]
  if (newType === 'fixed' && (item.value === undefined || item.value === null)) {
    item.value = 0
  }
  if (newType === 'calc' && !item.formula) {
    item.formula = ''
  }
}

// ── 加载 ─────────────────────────────────────────────────────────────────
async function loadConfig() {
  try {
    const res = await axios.get('/api/factor_config')
    config.value = res.data
  } catch (e) {
    ElMessage.error('加载因子配置失败: ' + e.message)
  }
}

// ── 保存 ─────────────────────────────────────────────────────────────────
async function saveAll() {
  saving.value = true
  try {
    const res = await axios.post('/api/factor_config', config.value)
    if (res.data.ok) {
      ElMessage.success('配置已保存，量能监控下次计算立即生效')
    } else {
      ElMessage.error('保存失败: ' + (res.data.error || '未知错误'))
    }
  } catch (e) {
    ElMessage.error('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}

// ── 重置 ─────────────────────────────────────────────────────────────────
async function resetAll() {
  try {
    await ElMessageBox.confirm('确定恢复所有因子为默认配置？当前修改将丢失。', '确认重置', {
      confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  try {
    const res = await axios.delete('/api/factor_config')
    if (res.data.ok) {
      config.value = res.data.config
      ElMessage.success('已恢复默认配置')
    }
  } catch (e) {
    ElMessage.error('重置失败: ' + e.message)
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.factor-wrap { padding: 16px; max-width: 1000px; }

.page-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 20px;
}
.page-title { font-size: 18px; font-weight: 700; color: #303133; }
.page-sub   { font-size: 12px; color: #909399; margin-top: 4px; }
.header-btns { display: flex; gap: 8px; }

.factors { display: flex; flex-direction: column; gap: 16px; }

.factor-card :deep(.el-card__header) { padding: 12px 16px; }
.factor-card :deep(.el-card__body)   { padding: 16px; }

.factor-header { display: flex; align-items: center; gap: 12px; }
.factor-tag    { font-size: 16px; font-weight: 700; padding: 6px 14px; }
.factor-title-block { flex: 1; }
.factor-name   { font-size: 15px; font-weight: 600; margin-right: 10px; }
.factor-desc   { font-size: 12px; color: #909399; }

.formula-alert { margin-bottom: 14px; }
.formula-alert :deep(.el-alert__title) { font-size: 12px; font-family: monospace; }

.section-title {
  display: flex; align-items: center; gap: 5px;
  font-size: 13px; font-weight: 600; color: #303133;
  margin: 14px 0 8px;
}
.veto-title { color: #f56c6c; }

.th-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; background: #fafafa; padding: 6px 10px; border-radius: 4px;
  border: 1px solid #f0f0f0;
}
.th-label { font-size: 12px; color: #606266; flex: 1; }

.scores-table { width: 100%; }
.score-label { display: flex; align-items: center; }

.formula-input-wrap { display: flex; flex-direction: column; gap: 3px; }
.formula-hint { font-size: 11px; color: #909399; }

.disabled-mask { opacity: 0.45; pointer-events: none; }
</style>
