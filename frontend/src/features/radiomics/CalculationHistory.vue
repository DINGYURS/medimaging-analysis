<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import api from '@/api/client'
import LiverSupplement from './LiverSupplement.vue'
import { activeStatus, binsSummary, exportable, featureName, gridSummary, interpolationSummary, normalizationSummary, normalizationAndOrderSummary, recordTime, resegmentationSummary, statusLabel, type CalculationRun } from './history'

const props = defineProps<{ taskId: number | null; submittedRun: CalculationRun | null }>()
const emit = defineEmits<{ 'latest-run': [run: CalculationRun | null]; loading: [value: boolean] }>()
const records = ref<CalculationRun[]>([]), selectedId = ref(''), selectedRun = ref<CalculationRun | null>(null)
const loading = ref(false), loadingMore = ref(false), loadingDetail = ref(false), hasMore = ref(false)
const listError = ref(''), detailError = ref(''), exportError = ref(''), pollingError = ref('')
const exporting = ref<'csv' | 'excel' | null>(null), activeRun = ref<CalculationRun | null>(null)
let generation = 0, listRevision = 0, detailRevision = 0
let timer: ReturnType<typeof setTimeout> | undefined
const PAGE_SIZE = 20
const endpoint = (id: number) => '/results/' + id + '/feature-calculation'
const canExport = computed(() => !loadingDetail.value && !exporting.value && selectedId.value === selectedRun.value?.id && exportable(selectedRun.value))
const selectedKeys = computed(() => selectedRun.value?.config?.features?.keys || [])
const detailPanel = ref<HTMLElement | null>(null), reservedDetailHeight = ref(0)
const selectedSummary = computed(() => selectedRun.value || records.value.find(run => run.id === selectedId.value) || null)
function reserveDetailSpace() {
  const height = detailPanel.value?.getBoundingClientRect().height || 0
  if (height > 0) reservedDetailHeight.value = Math.ceil(height)
}
const outsidePage = computed(() => selectedId.value && !records.value.some(r => r.id === selectedId.value))
function preferenceKey(id: number) {
  let owner = 'current'
  try { owner = String(JSON.parse(localStorage.getItem('user') || '{}').id || owner) } catch {}
  return `radiomics-history-selection:${owner}:${id}`
}
function remembered(id: number) { try { return sessionStorage.getItem(preferenceKey(id)) || '' } catch { return '' } }
function remember(id: number, runId: string) { try { sessionStorage.setItem(preferenceKey(id), runId) } catch {} }
function updateSummary(run: CalculationRun) {
  records.value = records.value.map(row => row.id === run.id ? { ...row, ...run } : row)
}
function applySelected(run: CalculationRun) {
  if (selectedId.value !== run.id) return
  if (selectedRun.value && !activeStatus(selectedRun.value.status) && activeStatus(run.status)) return
  selectedRun.value = run
}
function stopPolling() { if (timer) clearTimeout(timer); timer = undefined }
async function poll(id: number, runId: string, scope: number) {
  stopPolling()
  try {
    const result = await api.get(endpoint(id) + '/runs/' + runId) as unknown as CalculationRun
    if (scope !== generation || props.taskId !== id) return
    updateSummary(result); applySelected(result); emit('latest-run', result); pollingError.value = ''
    activeRun.value = activeStatus(result.status) ? result : null
    if (activeRun.value) timer = setTimeout(() => void poll(id, runId, scope), 2000)
  } catch (error: any) {
    if (scope !== generation) return
    pollingError.value = error.message || '计算进度读取失败'
    timer = setTimeout(() => void poll(id, runId, scope), 5000)
  }
}
async function choose(runId: string) {
  if (!props.taskId || exporting.value) return false
  const id = props.taskId, scope = generation, revision = ++detailRevision
  reserveDetailSpace()
  selectedId.value = runId; selectedRun.value = null; loadingDetail.value = true; detailError.value = ''; exportError.value = ''
  try {
    const result = await api.get(endpoint(id) + '/runs/' + runId) as unknown as CalculationRun
    if (scope !== generation || revision !== detailRevision || props.taskId !== id) return false
    applySelected(result); remember(id, result.id); return true
  } catch (error: any) {
    if (scope === generation && revision === detailRevision) detailError.value = error.message || '记录详情读取失败，请重试'
    return false
  } finally { if (scope === generation && revision === detailRevision) loadingDetail.value = false }
}
async function reload(preferred = selectedId.value) {
  if (!props.taskId) return
  const id = props.taskId, scope = generation, revision = ++listRevision
  loading.value = true; loadingMore.value = false; listError.value = ''; emit('loading', true)
  try {
    const response = await api.get(endpoint(id) + `/runs?offset=0&limit=${PAGE_SIZE + 1}`) as unknown as CalculationRun[]
    if (scope !== generation || revision !== listRevision || props.taskId !== id) return
    records.value = response.slice(0, PAGE_SIZE); hasMore.value = response.length > PAGE_SIZE
    const latest = records.value[0] || null; emit('latest-run', latest); stopPolling()
    activeRun.value = latest && activeStatus(latest.status) ? latest : null
    if (activeRun.value) void poll(id, activeRun.value.id, scope)
    if (preferred || latest) await choose(preferred || latest!.id)
    else { selectedId.value = ''; selectedRun.value = null }
  } catch (error: any) { if (scope === generation && revision === listRevision) listError.value = error.message || '计算记录读取失败' }
  finally { if (scope === generation && revision === listRevision) { loading.value = false; emit('loading', false) } }
}
async function more() {
  if (!props.taskId || loading.value || loadingMore.value) return
  const id = props.taskId, scope = generation, revision = listRevision
  loadingMore.value = true; listError.value = ''
  try {
    const response = await api.get(endpoint(id) + `/runs?offset=${records.value.length}&limit=${PAGE_SIZE + 1}`) as unknown as CalculationRun[]
    if (scope !== generation || revision !== listRevision || props.taskId !== id) return
    const known = new Set(records.value.map(r => r.id))
    records.value.push(...response.slice(0, PAGE_SIZE).filter(r => !known.has(r.id))); hasMore.value = response.length > PAGE_SIZE
  } catch (error: any) { if (scope === generation) listError.value = error.message || '更多记录读取失败' }
  finally { if (scope === generation && revision === listRevision) loadingMore.value = false }
}
async function download(format: 'csv' | 'excel') {
  if (!canExport.value || !props.taskId || !selectedRun.value) return
  // Capture both scope and run before awaiting; selection cannot rename or
  // redirect an in-flight export to a different record.
  const id = props.taskId, runId = selectedRun.value.id, scope = generation
  exporting.value = format; exportError.value = ''
  try {
    const response = await fetch('/api/v1' + endpoint(id) + '/runs/' + runId + '/export?format=' + format, { headers: { Authorization: 'Bearer ' + (localStorage.getItem('token') || '') } })
    if (!response.ok) throw new Error('该记录导出失败，请重试')
    const blob = await response.blob()
    if (scope !== generation || props.taskId !== id) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = 'radiomics_' + runId + (format === 'csv' ? '.csv' : '.xlsx'); document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error: any) { if (scope === generation) exportError.value = error.message || '导出失败，请重试' }
  finally { if (scope === generation) exporting.value = null }
}
watch(() => props.taskId, id => {
  generation++; listRevision++; detailRevision++; stopPolling()
  records.value = []; selectedId.value = ''; selectedRun.value = null; activeRun.value = null; reservedDetailHeight.value = 0
  loading.value = false; loadingMore.value = false; loadingDetail.value = false; hasMore.value = false; exporting.value = null
  listError.value = ''; detailError.value = ''; exportError.value = ''; pollingError.value = ''; emit('latest-run', null); emit('loading', false)
  if (id) void reload(remembered(id))
}, { immediate: true })
watch(() => props.submittedRun, run => {
  if (!run || run.analysis_task_id !== props.taskId) return
  selectedId.value = run.id; selectedRun.value = run; remember(props.taskId!, run.id)
  void reload(run.id)
})
onBeforeUnmount(() => { generation++; detailRevision++; stopPolling() })
</script>

<template>
  <section id="calculation-history" class="history-section" aria-labelledby="history-heading">
    <header class="history-heading">
      <div><h2 id="history-heading">计算记录与导出</h2><p class="c-small c-muted">选择一次计算，查看当次保存的特征、参数和结果后导出。</p></div>
      <div class="c-row"><span v-if="activeRun" class="c-badge warning">{{ statusLabel(activeRun.status) }} · {{ activeRun.completed_lesions }} / {{ activeRun.total_lesions }} 个病灶</span><button type="button" class="c-button" :disabled="!taskId || loading || !!exporting" @click="reload()">{{ loading ? '读取记录中…' : '刷新记录' }}</button></div>
    </header>
    <p v-if="listError" class="c-alert" role="alert">{{ listError }}</p>
    <p v-if="pollingError" class="c-alert" role="alert">{{ pollingError }}，正在重试。</p>
    <div v-if="records.length" class="history-table-scroll">
      <table class="history-table" aria-label="当前分析任务的特征计算记录">
        <colgroup><col style="width:52px" /><col style="width:180px" /><col style="width:90px" /><col style="width:140px" /><col style="width:180px" /><col /><col style="width:80px" /><col style="width:110px" /></colgroup>
        <thead><tr><th class="choice-column">选择</th><th>计算时间</th><th>目标特征</th><th>分析网格</th><th>归一化</th><th>离散化</th><th>病灶</th><th>状态</th></tr></thead>
        <tbody><tr v-for="record in records" :key="record.id" :class="{ selected: selectedId === record.id }" :aria-selected="selectedId === record.id" @click="choose(record.id)">
          <td class="choice-column"><input type="radio" name="calculation-record" :value="record.id" :checked="selectedId === record.id" :disabled="!!exporting" :aria-label="`选择 ${recordTime(record.created_at)} · ${record.selected_count}项特征`" @click.stop @change="choose(record.id)" /></td>
          <td><strong>{{ recordTime(record.created_at) }}</strong></td>
          <td><strong>{{ record.selected_count }} 项</strong></td>
          <td>{{ gridSummary(record.config) }}</td><td>{{ normalizationSummary(record.config) }}</td><td class="bins-cell"><span class="bin-summary" :title="binsSummary(record.config)">{{ binsSummary(record.config) }}</span></td>
          <td>{{ record.completed_lesions }} / {{ record.total_lesions }}</td><td><span class="c-badge" :class="record.status === 'succeeded' ? 'active' : record.status === 'partial' || activeStatus(record.status) ? 'warning' : ''">{{ statusLabel(record.status) }}</span></td>
        </tr></tbody>
      </table>
    </div>
    <p v-else class="history-empty c-muted" role="status">{{ loading ? '正在读取计算记录…' : !taskId ? '选择已完成分析的检查后查看计算记录。' : '当前分析任务暂无特征计算记录。完成一次计算后，将在此保留独立记录。' }}</p>
    <div v-if="hasMore" class="history-more"><button type="button" class="c-button" :disabled="loading || loadingMore || !!exporting" @click="more()">{{ loadingMore ? '读取中…' : '加载更早记录' }}</button></div>
    <p v-if="outsidePage" class="c-small c-muted">已恢复之前选中的历史记录；加载更早记录可查看其列表位置。</p>
    <p v-if="selectedId" class="sr-only" role="status" aria-live="polite">{{ loadingDetail ? '正在加载所选记录的方案与结果。' : detailError ? '所选记录加载失败，可以重试。' : selectedRun ? `所选记录已加载，${selectedRun.selected_count}项特征。` : '' }}</p>
    <div v-if="selectedId" ref="detailPanel" class="record-detail" :class="{ 'is-loading': loadingDetail, 'is-error': !!detailError }" :style="{ '--detail-reserved-height': reservedDetailHeight ? reservedDetailHeight + 'px' : undefined }" :aria-busy="loadingDetail">
      <div class="record-detail-heading"><div><h3>所选记录<span v-if="selectedSummary"> · {{ recordTime(selectedSummary.created_at) }}</span></h3><p v-if="selectedSummary" class="c-small c-muted">{{ statusLabel(selectedSummary.status) }} · {{ selectedSummary.selected_count }} 项目标 · {{ selectedSummary.total_lesions }} 个病灶</p><span v-else class="detail-skeleton-line skeleton-subtitle" aria-hidden="true"></span></div><div class="c-row"><button type="button" class="c-button" :disabled="!canExport" @click="download('csv')">{{ exporting === 'csv' ? '正在导出…' : '导出 CSV' }}</button><button type="button" class="c-button" :disabled="!canExport" @click="download('excel')">{{ exporting === 'excel' ? '正在导出…' : '导出 Excel' }}</button></div></div>
      <template v-if="loadingDetail">
        <p class="detail-loading"><span class="detail-loading-spinner" aria-hidden="true"></span>正在加载该次计算的方案与结果…</p>
        <div class="detail-skeleton" aria-hidden="true"><div class="saved-summary skeleton-summary"><div v-for="index in 6" :key="index"><span class="detail-skeleton-line skeleton-label"></span><span class="detail-skeleton-line skeleton-value" :class="{ 'skeleton-value-short': index % 3 === 0 }"></span></div></div><span class="detail-skeleton-line skeleton-result"></span><span class="detail-skeleton-line skeleton-disclosure"></span></div>
      </template>
      <p v-else-if="detailError" class="c-alert detail-load-error" role="alert">{{ detailError }} <button type="button" class="c-link" @click="choose(selectedId)">重试读取</button></p>
      <template v-else-if="selectedRun">
      <dl v-if="selectedRun.config" class="saved-summary">
        <div><dt>当次目标特征</dt><dd>{{ selectedRun.selected_count }} 项</dd></div>
        <div><dt>源影像读取</dt><dd>{{ selectedRun.config.source_version === 'physical-v1' ? '按源文件读取空间信息与强度' : '历史兼容读取' }}</dd></div>
        <div><dt>分析网格与插值</dt><dd>{{ gridSummary(selectedRun.config) }} · {{ interpolationSummary(selectedRun.config) }}</dd></div>
        <div><dt>强度重分割</dt><dd>{{ resegmentationSummary(selectedRun.config) }}</dd></div>
        <div><dt>归一化与处理顺序</dt><dd>{{ normalizationAndOrderSummary(selectedRun) }}</dd></div>
        <div><dt>离散化</dt><dd>{{ binsSummary(selectedRun.config) }}</dd></div><div><dt>肝脏补充特征</dt><dd>{{ selectedRun.config.include_liver_features ? '按适用模态追加' : '关闭' }}</dd></div>
      </dl>
      <p v-if="selectedRun.error_summary?.reason" class="c-alert">{{ selectedRun.error_summary.reason }}</p>
      <div v-for="row in selectedRun.results || []" :key="row.lesion_id" class="lesion-result"><strong>病灶 #{{ row.lesion_index }}</strong><span>{{ Object.keys(row.features).length }} 项已计算，{{ Object.keys(row.unavailable).length }} 项不可计算</span><details v-if="Object.keys(row.unavailable).length"><summary>查看不可计算原因</summary><p v-for="(reason,key) in row.unavailable" :key="key"><strong>{{ key }}</strong>：{{ reason }}</p></details><LiverSupplement v-if="row.provenance?.liver_supplement" :result="row.provenance.liver_supplement" /></div>
      <p v-if="!exportable(selectedRun)" class="c-small c-muted">{{ activeStatus(selectedRun.status) ? '计算完成后可导出该记录。' : '该记录未生成可导出的结果，可查看当次方案及失败原因。' }}</p>
      <details v-if="selectedKeys.length" class="record-disclosure"><summary>查看当次选择的 {{ selectedKeys.length }} 项特征</summary><ul class="saved-features"><li v-for="key in selectedKeys" :key="key"><span>{{ featureName(key) }}</span><code>{{ key }}</code></li></ul></details>
      <p v-if="exportError" class="c-alert" role="alert">{{ exportError }}</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.history-section{padding:20px;border:1px solid var(--c-line);border-radius:12px;background:var(--c-panel);box-shadow:0 1px 2px #00000005}.history-heading,.record-detail-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.history-heading{padding-bottom:16px}.history-heading h2{font-size:16px;margin:0 0 5px}.history-heading p,.record-detail-heading p{margin:0}.history-table-scroll{overflow-x:auto;border-top:1px solid var(--c-line);border-bottom:1px solid var(--c-line)}.history-table{width:100%;min-width:1000px;table-layout:fixed;border-collapse:collapse;font-size:12px;text-align:left}.history-table th{color:var(--c-muted);font-weight:500;padding:11px 10px;background:var(--c-soft)}.history-table td{padding:12px 10px;vertical-align:middle;border-top:1px solid var(--c-line);line-height:1.6}.history-table tbody tr{cursor:pointer;transition:background .15s}.history-table tbody tr:hover,.history-table tbody tr.selected{background:var(--c-blue-soft)}.history-table small{display:block;font-size:11px;margin-top:3px}.choice-column{width:46px;min-width:46px;white-space:nowrap;text-align:center}.choice-column input{width:16px;height:16px;accent-color:var(--c-blue);cursor:pointer}.bin-summary{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.history-table .c-badge{white-space:nowrap}.history-empty{padding:24px 0 8px;text-align:center;font-size:13px}.history-more{text-align:center;padding-top:14px}.record-detail{margin-top:22px;padding-top:20px;border-top:1px solid var(--c-line)}.record-detail h3{font-size:14px;margin:0 0 6px}.saved-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 28px;margin:16px 0}.saved-summary div{min-width:0}.saved-summary dt{font-size:11px;color:var(--c-muted);margin-bottom:4px}.saved-summary dd{font-size:12px;font-weight:600;line-height:1.7;overflow-wrap:anywhere;margin:0}.lesion-result{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;padding:10px 0}.lesion-result details{flex-basis:100%;color:var(--c-muted)}.lesion-result p{margin:7px 0}.record-disclosure{font-size:12px;margin-top:12px;color:var(--c-muted)}summary{cursor:pointer}.saved-features{max-height:240px;overflow:auto;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 18px;padding:12px 0;list-style:none}.saved-features li{display:flex;flex-direction:column;gap:2px;overflow-wrap:anywhere}.saved-features code{font-size:10px}.record-detail{box-sizing:border-box}.record-detail.is-loading,.record-detail.is-error{min-height:var(--detail-reserved-height,360px)}.detail-loading{display:flex;align-items:center;gap:8px;margin:16px 0 0;color:var(--c-muted);font-size:12px}.detail-loading-spinner{width:13px;height:13px;border:2px solid var(--c-line);border-top-color:var(--c-blue);border-radius:50%;animation:detail-spin .8s linear infinite;flex:none}.detail-skeleton-line{display:block;height:13px;border-radius:4px;background:var(--c-soft);animation:detail-pulse 1.6s ease-in-out infinite}.skeleton-subtitle{width:150px;margin-top:7px}.skeleton-summary{margin-top:18px;gap:16px 28px}.skeleton-label{width:76px;height:10px;margin-bottom:8px}.skeleton-value{width:78%;max-width:360px}.skeleton-value-short{width:48%}.skeleton-result{width:245px;max-width:75%;margin:22px 0}.skeleton-disclosure{width:180px;max-width:70%;height:11px;margin-top:18px}.detail-load-error{margin-top:20px}@keyframes detail-spin{to{transform:rotate(360deg)}}@keyframes detail-pulse{50%{opacity:.45}}@media(max-width:700px){.record-detail.is-loading,.record-detail.is-error{min-height:var(--detail-reserved-height,540px)}}@media(prefers-reduced-motion:reduce){.detail-loading-spinner,.detail-skeleton-line{animation:none}}.c-alert{margin:12px 0}.c-button:not(:disabled):hover{background:var(--c-blue-soft);border-color:var(--c-line);color:var(--c-blue)}@media(max-width:700px){.history-section{padding:16px}.saved-summary,.saved-features{grid-template-columns:1fr}.record-detail-heading{align-items:flex-start}}
</style>
