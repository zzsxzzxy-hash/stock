<template>
  <div class="sector-mgr">
    <!-- 顶部操作栏 -->
    <div class="search-row">
      <el-input v-model="searchCode" placeholder="输入股票代码搜索" clearable
                @input="onSearchCode" style="width:200px" />
      <el-input v-model="searchName" placeholder="输入股票名称搜索" clearable
                @input="onSearchName" style="width:200px; margin-left:8px" />
      <el-divider direction="vertical" style="margin:0 12px;height:28px" />
      <el-button
        type="success"
        :icon="Refresh"
        :loading="syncLoading"
        @click="syncSectors"
      >同步板块数据（东财）</el-button>
      <el-tag v-if="syncMsg" :type="syncMsgType" style="margin-left:10px" effect="light">{{ syncMsg }}</el-tag>
      <el-button :icon="Plus" type="primary" @click="openAddBatch" style="margin-left:auto">
        批量添加股票到板块
      </el-button>
    </div>

    <!-- 板块列表（左）+ 板块内股票（右） -->
    <div class="sector-layout">
      <!-- 左：板块列表 -->
      <div class="sector-list">
        <div class="panel-title">
          板块列表
          <el-button size="small" :icon="Plus" @click="openAddSector" circle style="margin-left:8px" />
        </div>
        <div v-for="s in sectorList" :key="s.sector"
             class="sector-item" :class="{ active: activeSector === s.sector }"
             @click="selectSector(s.sector)">
          <span class="sector-name">{{ s.sector }}</span>
          <el-tag size="small" type="info">{{ s.count }}只</el-tag>
        </div>
        <div v-if="!sectorList.length" class="empty">暂无板块，点击+新建</div>
      </div>

      <!-- 右：板块内股票 -->
      <div class="sector-stocks">
        <div class="panel-title" v-if="activeSector">
          {{ activeSector }} 板块股票
          <el-button size="small" :icon="Plus" @click="openAddStock" style="margin-left:8px">
            添加股票
          </el-button>
        </div>
        <div v-if="activeSector">
          <el-table :data="stocksInSector" size="small" height="360" v-loading="stockLoading">
            <el-table-column prop="code" label="代码" width="90" />
            <el-table-column prop="name" label="名称" />
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button link type="danger" size="small" @click="removeStock(row.code)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div v-else class="empty">← 点击左侧板块查看股票</div>
      </div>

      <!-- 股票的板块编辑（搜索结果） -->
      <div class="stock-sectors" v-if="searchResult">
        <div class="panel-title">
          {{ searchResult.code }} {{ searchResult.name }} 的板块
        </div>
        <div class="tag-list">
          <el-tag
            v-for="s in searchResult.sectors" :key="s"
            closable @close="removeSectorFromStock(searchResult.code, s)"
            style="margin:3px"
          >{{ s }}</el-tag>
        </div>
        <el-select v-model="newSectorForStock" filterable allow-create placeholder="选择或输入板块名"
                   style="width:200px; margin-top:8px">
          <el-option v-for="s in sectorList" :key="s.sector" :label="s.sector" :value="s.sector" />
        </el-select>
        <el-button type="primary" size="small" @click="addSectorToStock" style="margin-left:8px">
          添加
        </el-button>
      </div>
    </div>

    <!-- 新建板块弹窗 -->
    <el-dialog v-model="addSectorVisible" title="新建板块" width="360px">
      <el-input v-model="newSectorName" placeholder="板块名称，如：机器人" clearable />
      <template #footer>
        <el-button @click="addSectorVisible = false">取消</el-button>
        <el-button type="primary" @click="createSector">确认</el-button>
      </template>
    </el-dialog>

    <!-- 添加股票到当前板块 -->
    <el-dialog v-model="addStockVisible" :title="`添加股票到 ${activeSector}`" width="360px">
      <el-input v-model="addStockCode" placeholder="股票代码，如：000001" clearable />
      <template #footer>
        <el-button @click="addStockVisible = false">取消</el-button>
        <el-button type="primary" @click="doAddStock">确认</el-button>
      </template>
    </el-dialog>

    <!-- 批量添加 -->
    <el-dialog v-model="batchVisible" title="批量添加股票到板块" width="480px">
      <el-form label-width="80px">
        <el-form-item label="板块">
          <el-select v-model="batchSector" filterable allow-create placeholder="选择或新建板块" style="width:100%">
            <el-option v-for="s in sectorList" :key="s.sector" :label="s.sector" :value="s.sector" />
          </el-select>
        </el-form-item>
        <el-form-item label="股票代码">
          <el-input v-model="batchCodes" type="textarea" :rows="5"
                    placeholder="每行一个代码，或逗号分隔&#10;例：000001&#10;000002&#10;600000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button type="primary" @click="doBatchAdd" :loading="batchLoading">确认添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const sectorList     = ref([])
const activeSector   = ref('')
const stocksInSector = ref([])
const stockLoading   = ref(false)

const searchCode    = ref('')
const searchName    = ref('')
const searchResult  = ref(null)

const addSectorVisible = ref(false)
const newSectorName    = ref('')

const addStockVisible  = ref(false)
const addStockCode     = ref('')

const batchVisible  = ref(false)
const batchSector   = ref('')
const batchCodes    = ref('')
const batchLoading  = ref(false)

const newSectorForStock = ref('')

// ── 同步板块数据 ───────────────────────────────────────────
const syncLoading = ref(false)
const syncMsg     = ref('')
const syncMsgType = ref('info')

async function syncSectors() {
  syncLoading.value = true
  syncMsg.value = '同步中，约需1-2分钟…'
  syncMsgType.value = 'warning'
  try {
    const res = await axios.post('/api/system_action', { action: 'reload_sectors' })
    if (res.data.ok) {
      syncMsg.value = res.data.msg
      syncMsgType.value = 'success'
      // 3秒后刷新板块列表
      setTimeout(async () => {
        await loadSectors()
        syncMsg.value = `已完成，共 ${sectorList.value.length} 个板块`
      }, 3000)
    } else {
      syncMsg.value = res.data.msg || '同步失败'
      syncMsgType.value = 'danger'
    }
  } catch (e) {
    syncMsg.value = '请求失败: ' + e.message
    syncMsgType.value = 'danger'
  } finally {
    syncLoading.value = false
  }
}

// ── 加载板块列表 ──────────────────────────────────────────
async function loadSectors() {
  try {
    const res = await axios.get('/api/sector_list')
    sectorList.value = res.data.data || []
  } catch { /* ignore */ }
}

// ── 选择板块 ───────────────────────────────────────────────
async function selectSector(sector) {
  activeSector.value = sector
  stockLoading.value = true
  try {
    const res = await axios.get('/api/sector_stocks', { params: { sector } })
    stocksInSector.value = res.data.data || []
  } catch {
    ElMessage.error('加载失败')
  } finally {
    stockLoading.value = false
  }
}

// ── 移除股票 ───────────────────────────────────────────────
async function removeStock(code) {
  await axios.delete('/api/sector_map', { data: { code, sector: activeSector.value } })
  await selectSector(activeSector.value)
  await loadSectors()
  ElMessage.success('已移除')
}

// ── 搜索股票并显示其板块 ────────────────────────────────────
let searchTimer = null
async function onSearchCode() {
  clearTimeout(searchTimer)
  if (!searchCode.value && !searchName.value) {
    searchResult.value = null
    return
  }
  searchTimer = setTimeout(_doSearch, 400)
}

async function onSearchName() {
  clearTimeout(searchTimer)
  if (!searchCode.value && !searchName.value) {
    searchResult.value = null
    return
  }
  searchTimer = setTimeout(_doSearch, 400)
}

async function _doSearch() {
  try {
    const code = searchCode.value.trim()
    if (!code) return
    const [mapRes, nameRows] = await Promise.all([
      axios.get('/api/sector_map/stock', { params: { code } }),
      axios.get('/api/data', { params: { name: 'cn_stock_spot', search: code, size: 1 } }),
    ])
    const name = nameRows.data?.data?.[0]?.name || ''
    searchResult.value = {
      code,
      name,
      sectors: mapRes.data.sectors || [],
    }
  } catch { /* ignore */ }
}

async function addSectorToStock() {
  if (!searchResult.value || !newSectorForStock.value) return
  await axios.post('/api/sector_map', { code: searchResult.value.code, sector: newSectorForStock.value })
  searchResult.value.sectors.push(newSectorForStock.value)
  newSectorForStock.value = ''
  await loadSectors()
  ElMessage.success('已添加')
}

async function removeSectorFromStock(code, sector) {
  await axios.delete('/api/sector_map', { data: { code, sector } })
  searchResult.value.sectors = searchResult.value.sectors.filter(s => s !== sector)
  await loadSectors()
  ElMessage.success('已移除')
}

// ── 新建板块 ───────────────────────────────────────────────
function openAddSector() { addSectorVisible.value = true; newSectorName.value = '' }
async function createSector() {
  if (!newSectorName.value.trim()) return
  // 板块通过添加一个占位记录来创建（或直接通过 batch）
  // 这里先关闭弹窗，让用户自己往里加股票
  addSectorVisible.value = false
  activeSector.value = newSectorName.value.trim()
  stocksInSector.value = []
  // 把板块名加到本地列表
  if (!sectorList.value.find(s => s.sector === activeSector.value)) {
    sectorList.value.push({ sector: activeSector.value, count: 0 })
  }
}

// ── 添加股票到当前板块 ─────────────────────────────────────
function openAddStock() { addStockVisible.value = true; addStockCode.value = '' }
async function doAddStock() {
  const code = addStockCode.value.trim()
  if (!code || !activeSector.value) return
  try {
    await axios.post('/api/sector_map', { code, sector: activeSector.value })
    addStockVisible.value = false
    await selectSector(activeSector.value)
    await loadSectors()
    ElMessage.success('已添加')
  } catch (e) {
    ElMessage.error('添加失败')
  }
}

// ── 批量添加 ───────────────────────────────────────────────
function openAddBatch() { batchVisible.value = true; batchSector.value = ''; batchCodes.value = '' }
async function doBatchAdd() {
  if (!batchSector.value || !batchCodes.value) return
  batchLoading.value = true
  const codes = batchCodes.value
    .split(/[\n,，\s]+/)
    .map(c => c.trim())
    .filter(c => /^\d{6}$/.test(c))
  let ok = 0
  for (const code of codes) {
    try {
      await axios.post('/api/sector_map', { code, sector: batchSector.value })
      ok++
    } catch { /* ignore */ }
  }
  batchLoading.value = false
  batchVisible.value = false
  await loadSectors()
  if (activeSector.value === batchSector.value) await selectSector(activeSector.value)
  ElMessage.success(`成功添加 ${ok}/${codes.length} 只股票`)
}

onMounted(loadSectors)
</script>

<style scoped>
.sector-mgr { padding: 4px 0; }
.search-row { display: flex; align-items: center; margin-bottom: 16px; }
.sector-layout { display: grid; grid-template-columns: 200px 1fr 280px; gap: 12px; }
.panel-title { font-weight: 600; font-size: 13px; margin-bottom: 8px;
  display: flex; align-items: center; }
.sector-list { border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px; overflow-y: auto; max-height: 400px; }
.sector-item { display: flex; align-items: center; justify-content: space-between;
  padding: 6px 8px; border-radius: 4px; cursor: pointer; margin-bottom: 2px; }
.sector-item:hover { background: #f5f7fa; }
.sector-item.active { background: #ecf5ff; }
.sector-name { font-size: 13px; }
.empty { color: #909399; font-size: 12px; padding: 12px 0; }
.sector-stocks { border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px; }
.stock-sectors { border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px; }
.tag-list { display: flex; flex-wrap: wrap; }
</style>
