<template>
  <div class="trade-theme-mgr">
    <div class="toolbar">
      <el-input
        v-model="searchText"
        placeholder="搜索代码/名称/交易主线"
        clearable
        :prefix-icon="Search"
        style="width: 260px"
        @input="onSearch"
      />
      <el-button :icon="Refresh" :loading="loading" @click="reloadAll">刷新</el-button>
      <el-text type="info" size="small">策略实际使用的一股一交易主线</el-text>
    </div>

    <div class="layout">
      <aside class="sector-list">
        <div class="panel-title">交易主线</div>
        <div
          class="sector-item"
          :class="{ active: activeSector === '' }"
          @click="selectSector('')"
        >
          <span>全部</span>
          <el-tag size="small" type="info">{{ totalStocks }}</el-tag>
        </div>
        <div
          v-for="s in sectorList"
          :key="s.sector"
          class="sector-item"
          :class="{ active: activeSector === s.sector }"
          @click="selectSector(s.sector)"
        >
          <span class="sector-name">{{ s.sector }}</span>
          <el-tag size="small" type="info">{{ s.count }}</el-tag>
        </div>
      </aside>

      <main class="stock-panel">
        <div class="panel-title">
          {{ activeSector || '全部股票' }}
          <el-text type="info" size="small">共 {{ total }} 只</el-text>
        </div>
        <el-table
          v-loading="loading"
          :data="rows"
          border
          stripe
          size="small"
          height="560"
          row-key="code"
        >
          <el-table-column label="股票" width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="stock-cell">
                <span class="stock-code">{{ row.code || '—' }}</span>
                <span class="stock-name">{{ row.name || row.code || '—' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="交易主线" min-width="220">
            <template #default="{ row }">
              <el-select
                v-model="row._draftSector"
                filterable
                allow-create
                default-first-option
                size="small"
                style="width: 100%"
                :loading="row._loadingOptions"
                @focus="loadOptions(row)"
                @change="saveRow(row)"
              >
                <el-option
                  v-for="s in row._options || [row.trade_theme].filter(Boolean)"
                  :key="s"
                  :label="s"
                  :value="s"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.source === 'manual' ? 'success' : 'info'" effect="plain">
                {{ row.source === 'manual' ? '人工' : '算法' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="confidence" label="置信度" width="90">
            <template #default="{ row }">{{ Number(row.confidence || 0).toFixed(1) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" min-width="260" show-overflow-tooltip />
          <el-table-column prop="updated_at" label="更新时间" width="165" show-overflow-tooltip />
        </el-table>

        <div class="pager">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="size"
            :total="total"
            :page-sizes="[100, 200, 500, 1000]"
            layout="total, sizes, prev, pager, next, jumper"
            background
            small
            @current-change="loadStocks"
            @size-change="onSizeChange"
          />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import axios from 'axios'

const sectorList = ref([])
const allSectorOptions = ref([])
const activeSector = ref('')
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(200)
const searchText = ref('')
const loading = ref(false)
let searchTimer = null

const totalStocks = computed(() => sectorList.value.reduce((sum, s) => sum + Number(s.count || 0), 0))

async function loadSectors() {
  const [themeRes, rawRes] = await Promise.all([
    axios.get('/api/trade_theme_list'),
    axios.get('/api/sector_list'),
  ])
  sectorList.value = themeRes.data.data || []
  const opts = new Set()
  for (const s of sectorList.value) opts.add(s.sector)
  for (const s of rawRes.data.data || []) opts.add(s.sector)
  allSectorOptions.value = [...opts].filter(Boolean).sort()
}

function normalizeRows(items) {
  return items.map(row => ({
    ...row,
    _draftSector: row.trade_theme || '',
    _options: row.trade_theme ? [row.trade_theme] : [],
    _loadingOptions: false,
    _loadedOptions: false,
  }))
}

async function loadStocks() {
  loading.value = true
  try {
    const params = {
      sector: activeSector.value,
      search: searchText.value.trim(),
      page: page.value,
      size: size.value,
    }
    const res = await axios.get('/api/trade_theme_stocks', { params })
    rows.value = normalizeRows(res.data.data || [])
    total.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('交易主线加载失败：' + (e.response?.data?.error || e.message))
  } finally {
    loading.value = false
  }
}

async function reloadAll() {
  await loadSectors()
  await loadStocks()
}

function selectSector(sector) {
  activeSector.value = sector
  page.value = 1
  loadStocks()
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadStocks()
  }, 350)
}

function onSizeChange() {
  page.value = 1
  loadStocks()
}

async function loadOptions(row) {
  if (row._loadedOptions) return
  row._loadingOptions = true
  try {
    const res = await axios.get('/api/trade_theme/stock', { params: { code: row.code } })
    const opts = new Set()
    for (const s of allSectorOptions.value) opts.add(s)
    const theme = res.data.theme || res.data.dominant
    if (theme?.sector) opts.add(theme.sector)
    for (const s of res.data.sectors || []) opts.add(s)
    if (row.trade_theme) opts.add(row.trade_theme)
    row._options = [...opts]
    row._loadedOptions = true
  } catch (e) {
    ElMessage.error('候选主线加载失败')
  } finally {
    row._loadingOptions = false
  }
}

async function saveRow(row) {
  const sector = String(row._draftSector || '').trim()
  if (!sector || sector === row.trade_theme) return
  row._loadingOptions = true
  try {
    await axios.put('/api/trade_theme/stock', { code: row.code, sector })
    row.trade_theme = sector
    row.source = 'manual'
    row.confidence = 100
    row.reason = '用户手动设置交易主线'
    row._options = [...new Set([sector, ...(row._options || [])])]
    ElMessage.success(`${row.code} ${row.name} 已改为 ${sector}`)
    await loadSectors()
    if (activeSector.value && activeSector.value !== sector) {
      await loadStocks()
    }
  } catch (e) {
    row._draftSector = row.trade_theme || ''
    ElMessage.error('保存失败：' + (e.response?.data?.error || e.message))
  } finally {
    row._loadingOptions = false
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.trade-theme-mgr { padding: 4px 0; }
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 12px;
}
.sector-list {
  border: 1px solid #ebeef5;
  background: #fff;
  padding: 10px;
  height: 620px;
  overflow-y: auto;
}
.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 700;
  color: #303133;
}
.sector-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px;
  cursor: pointer;
  border-bottom: 1px solid #f5f7fa;
}
.sector-item:hover { background: #f5f7fa; }
.sector-item.active { background: #ecf5ff; color: #409eff; }
.sector-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.stock-panel {
  min-width: 0;
  border: 1px solid #ebeef5;
  background: #fff;
  padding: 10px;
}
.stock-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.stock-code {
  flex: 0 0 auto;
  font-weight: 700;
  color: #409eff;
  font-family: Consolas, Monaco, monospace;
}
.stock-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #303133;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
</style>
