<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FeatureFormula from './FeatureFormula.vue'
import documentation from './ibsiFeatureInfo.json'
import customDescriptions from './customFeatureDescriptions.json'
import { featureFamilies } from './plan'
import type { RadiomicsFeatureDefinition } from './types'

const familyLabels: Record<string, string> = {
  morphology: 'Morphological', local_intensity: 'Local intensity',
  intensity_statistics: 'Intensity statistics', intensity_histogram: 'Intensity histogram',
  intensity_volume_histogram: 'Intensity-volume histogram',
  glcm: 'GLCM',
  glrlm: 'GLRLM',
  glszm: 'GLSZM',
  gldzm: 'GLDZM',
  ngtdm: 'NGTDM',
  ngldm: 'NGLDM',
  custom_lung_nodule: '自定义增量特征',
}
const familyLabel = (id: string) => familyLabels[id] ?? id

const props = defineProps<{ catalog: RadiomicsFeatureDefinition[] }>()
type Entry = { name: string; code: string; description: string; chinese: string; formula: string; variables: string[]; chinese_name: string; description_zh: string; source_url: string; variable_source_urls: string[] }
const info = documentation as Record<string, Entry>
const customInfo = customDescriptions as Record<string, { en: string; zh: string }>
const dialog = ref<HTMLDialogElement | null>(null)
const isOpen = ref(false)
const motion = ref<'opening' | 'closing' | ''>('')
const search = ref('')
const family = ref('')
const activeKey = ref('')
let paintFrame = 0
let resizeObserver: ResizeObserver | undefined
let lastWidth = 0
function scheduleCellPaint() {
  cancelAnimationFrame(paintFrame)
  if (!isOpen.value || !CSS.supports('content-visibility', 'auto')) return
  paintFrame = requestAnimationFrame(() => {
    if (!dialog.value?.open || motion.value === 'closing') return
    const cells = [...dialog.value.querySelectorAll<HTMLElement>('.dictionary-cell')]
    // Batch writes, reads, then writes. No scroll/wheel handler or layout reads during scrolling.
    cells.forEach(cell => { cell.style.contentVisibility = 'visible' })
    const heights = cells.map(cell => getComputedStyle(cell).height)
    cells.forEach((cell, index) => {
      cell.style.containIntrinsicBlockSize = 'auto ' + heights[index]
      cell.style.contentVisibility = 'auto'
    })
  })
}
onMounted(() => {
  resizeObserver = new ResizeObserver(entries => {
    const width = entries[0]?.contentRect.width ?? 0
    if (Math.abs(width - lastWidth) > 1) {
      lastWidth = width
      scheduleCellPaint()
    }
  })
  if (dialog.value) resizeObserver.observe(dialog.value)
  void document.fonts.ready.then(() => { if (isOpen.value) scheduleCellPaint() })
})
onBeforeUnmount(() => {
  cancelAnimationFrame(paintFrame)
  resizeObserver?.disconnect()
})
const entries = computed(() => featureFamilies.flatMap(group => props.catalog.filter(feature => feature.family === group.id)).map(feature => ({
  ...feature, document: info[feature.key],
  title: info[feature.key]?.name ?? feature.name,
  englishDescription: info[feature.key]?.description ?? customInfo[feature.key]?.en ?? 'No English description is available.',
  translatedDescription: info[feature.key]?.chinese ?? customInfo[feature.key]?.zh ?? '暂无英文说明。',
})))
const shown = computed(() => {
  if (!isOpen.value) return []
  const query = search.value.trim().toLocaleLowerCase()
  return entries.value.filter(item => (!family.value || item.family === family.value)
    && (!query || [item.title, item.code, item.document?.chinese_name, item.englishDescription, item.translatedDescription, item.document?.chinese, customChineseNames[item.key], item.definition].join(' ').toLocaleLowerCase().includes(query)))
})
watch(shown, () => { void nextTick(scheduleCellPaint) })
const families = computed(() => featureFamilies.filter(item => props.catalog.some(feature => feature.family === item.id)))
const customChineseNames: Record<string, string> = {}

async function open(key = '') {
  if (dialog.value?.open) return
  motion.value = 'opening'
  isOpen.value = true
  search.value = ''
  family.value = ''
  activeKey.value = key
  dialog.value?.showModal()
  await nextTick()
  const row = key ? [...(dialog.value?.querySelectorAll<HTMLElement>('[data-dictionary-key]') ?? [])].find(el => el.dataset.dictionaryKey === key) : null
  if (row) {
    row.scrollIntoView({ block: 'center', inline: 'nearest' })
    row.focus({ preventScroll: true })
  } else {
    dialog.value?.querySelector('.dictionary-scroll')?.scrollTo(0, 0)
  }
  const element = dialog.value
  if (element) {
    await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => undefined)))
    if (motion.value === 'opening') motion.value = ''
  }
}
async function close() {
  const element = dialog.value
  if (!element?.open || motion.value === 'closing') return
  motion.value = 'closing'
  await nextTick()
  await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => undefined)))
  element.close()
  motion.value = ''
}
defineExpose({ open })
</script>

<template>
    <dialog ref="dialog" class="feature-dictionary" :class="motion" @cancel.prevent="close" @close="isOpen = false" aria-labelledby="feature-dictionary-title" @click="event => { if (event.target === dialog) close() }">
      <header class="dictionary-heading">
        <div><h2 id="feature-dictionary-title">特征字典</h2><p>按名称、IBSI ID 或中英文说明查找，查看公式与变量定义</p></div>
        <button type="button" class="c-button" aria-label="关闭特征字典" @click="close">关闭</button>
      </header>
      <div class="dictionary-tools">
        <label>搜索特征<input v-model="search" type="search" placeholder="名称、IBSI ID、说明" /></label>
        <label>特征类别<select v-model="family" aria-label="特征类别"><option value="">全部类别</option><option v-for="item in families" :key="item.id" :value="item.id">{{ familyLabel(item.id) }}</option></select></label>
      </div>
      <div class="dictionary-scroll" tabindex="0" aria-label="特征字典表格">
        <table>
          <colgroup><col class="name-column" /><col class="meaning-column" /><col class="formula-column" /><col class="notes-column" /></colgroup>
          <thead><tr><th scope="col">特征名称 / IBSI ID</th><th scope="col">说明</th><th scope="col">计算公式</th><th scope="col">变量说明</th></tr></thead>
          <tbody>
            <template v-for="(item, index) in shown" :key="item.key">
            <tr v-if="!family && (index === 0 || item.family !== shown[index - 1]?.family)" class="family-group" :data-dictionary-family="item.family">
              <th colspan="4" scope="colgroup">{{ familyLabel(item.family) }}</th>
            </tr>
            <tr :data-dictionary-key="item.key" :class="{ 'active-entry': activeKey === item.key }" tabindex="-1">
              <td><div class="dictionary-cell"><div class="entry-title"><strong>{{ item.title }}</strong><code v-if="item.document">{{ item.document.code }}</code></div><small v-if="item.document || customChineseNames[item.key]" class="chinese-name">{{ item.document?.chinese_name ?? customChineseNames[item.key] }}</small><small v-if="item.status !== 'computed'" class="unavailable">不可选</small></div></td>
              <td><div class="dictionary-cell"><div class="description-en" lang="en">{{ item.englishDescription }}</div><div class="description-zh" lang="zh-CN">{{ item.translatedDescription }}</div></div></td>
              <td><div class="dictionary-cell"><FeatureFormula v-if="item.document" :formula="item.document.formula" :show-label="false" copyable /><template v-else>{{ item.definition }}<small class="custom-note">现有自定义特征定义</small></template></div></td>
              <td><div class="dictionary-cell">
                <div class="variable-notes" tabindex="0" :aria-label="item.title + ' 变量说明'">
                  <template v-if="item.document">
                    <ul><li v-for="(note, index) in item.document.variables" :key="index"><FeatureFormula :formula="note" :show-label="false" /></li></ul>
                    <div class="official-sources">
                      <a :href="item.document.source_url" target="_blank" rel="noopener noreferrer">IBSI 官方定义 ↗</a>
                      <a v-if="item.document.variable_source_urls[1] !== item.document.source_url" :href="item.document.variable_source_urls[1]" target="_blank" rel="noopener noreferrer">特征家族与通用定义 ↗</a>
                    </div>
                  </template>
                  <p v-else>沿用现有自定义特征说明；本条目不属于 IBSI 文档。</p>
                </div>
                <p v-if="item.status !== 'computed'" class="unavailable">{{ item.reason || '目录标记为不可计算' }}</p>
              </div></td>
            </tr>
            </template>
            <tr v-if="!shown.length"><td colspan="4" class="empty-state">没有匹配的特征，请调整搜索词或类别</td></tr>
          </tbody>
        </table>
      </div>
    </dialog>
</template>

<style scoped>
.feature-dictionary { margin: auto; width: min(1600px, 96vw); max-width: 96vw; height: 92vh; height: 92dvh; max-height: 92vh; max-height: 92dvh; box-sizing: border-box; padding: 0; border: 1px solid var(--c-line); border-radius: 12px; color: var(--c-text); background: var(--c-panel); box-shadow: 0 20px 80px #00000040; }
.feature-dictionary[open] { display: flex; flex-direction: column; }
.feature-dictionary::backdrop { background: #0f172a80; }
.feature-dictionary.opening { animation: dictionary-enter 180ms cubic-bezier(.2,.8,.2,1) both; }
.feature-dictionary.closing { animation: dictionary-leave 140ms ease-in both; pointer-events: none; }
.feature-dictionary.opening::backdrop { animation: dictionary-backdrop-enter 180ms ease-out both; }
.feature-dictionary.closing::backdrop { animation: dictionary-backdrop-leave 140ms ease-in both; }
@keyframes dictionary-enter { from { opacity: 0; transform: translateY(12px) scale(.985); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes dictionary-leave { from { opacity: 1; transform: translateY(0) scale(1); } to { opacity: 0; transform: translateY(8px) scale(.99); } }
@keyframes dictionary-backdrop-enter { from { background-color: transparent; } to { background-color: #0f172a80; } }
@keyframes dictionary-backdrop-leave { from { background-color: #0f172a80; } to { background-color: transparent; } }
@media (prefers-reduced-motion: reduce) {
  .feature-dictionary.opening, .feature-dictionary.closing,
  .feature-dictionary.opening::backdrop, .feature-dictionary.closing::backdrop { animation: none; }
}
.dictionary-heading .c-button { flex-shrink: 0; }
.dictionary-heading { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 20px 24px 12px; }
h2 { margin: 0; font-size: 22px; } .dictionary-heading p { margin: 6px 0 0; color: var(--c-muted); font-size: 15px; }
.dictionary-tools { flex-shrink: 0; display: flex; align-items: flex-end; flex-wrap: wrap; gap: 16px; padding: 8px 24px 16px; border-bottom: 1px solid var(--c-line); }
.dictionary-tools label { display: grid; gap: 6px; color: var(--c-muted); font-size: 14px; }
.dictionary-tools input, .dictionary-tools select { min-height: 36px; padding: 7px 10px; border: 1px solid var(--c-line); border-radius: 6px; background: var(--c-panel); color: var(--c-text); font: inherit; font-size: 15px; }
.dictionary-tools input { width: 280px; }
.dictionary-cell { display: flow-root; }
.dictionary-scroll { flex: 1; min-height: 0; overflow: auto; scrollbar-gutter: stable; }
table { width: 100%; min-width: 1060px; border-collapse: separate; border-spacing: 0; table-layout: fixed; font-size: 15px; line-height: 1.7; }
.name-column { width: 21%; } .meaning-column { width: 20%; } .formula-column { width: 30%; } .notes-column { width: 29%; }
thead th { position: sticky; top: 0; z-index: 1; background: var(--c-soft); color: var(--c-blue); text-align: left; }
th, td { padding: 16px; border-bottom: 1px solid var(--c-line); vertical-align: top; overflow-wrap: anywhere; }
td strong, td small, td code { display: block; } td code { margin-top: 5px; color: var(--c-blue); } td small { margin-top: 6px; color: var(--c-muted); font-size: 14px; }
.active-entry td { background: var(--c-blue-soft); } .active-entry td:first-child { box-shadow: inset 4px 0 var(--c-blue); }
tr:focus { outline: 2px solid var(--c-blue); outline-offset: -2px; }
.variable-notes { max-height: 260px; overflow: auto; } .variable-notes ul { padding-left: 16px; margin: 0; } .variable-notes li + li { margin-top: 8px; }
.feature-dictionary :deep(.feature-formula) { font-size: 15px; }
.feature-dictionary :deep(.formula-content .katex) { font-size: 16px; }
.feature-dictionary td:nth-child(3) :deep(.formula-content .katex) { font-size: 20px; }
.variable-notes :deep(.feature-formula) { margin-top: 0; }
.family-group th { background: var(--c-blue-soft); color: var(--c-blue); font-size: 16px; text-align: center; padding: 12px 16px; border-top: 1px solid var(--c-line); }
.family-group + tr td { border-top: 0; }
.dictionary-tools select { width: min(280px, calc(96vw - 48px)); max-width: 100%; }
.feature-dictionary :deep(.dropdown-menu) { font-size: 15px; }
.feature-dictionary :deep(.dropdown-option span) { white-space: normal; overflow-wrap: anywhere; } .variable-notes :deep(.formula-content) { padding-top: 0; }
.entry-title strong, .entry-title code { display: inline; }
.entry-title code { margin: 0 0 0 7px; white-space: nowrap; font-size: 14px; }
.chinese-name { color: var(--c-muted); }
.description-zh { margin-top: 10px; }
.official-sources { display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 10px; font-size: 14px; }
.official-sources a { color: var(--c-blue); text-underline-offset: 3px; }
.unavailable { color: var(--c-amber); } .empty-state { text-align: center; padding: 48px; color: var(--c-muted); }
@media(max-width: 640px) { .dictionary-heading { padding: 16px; } .dictionary-tools { padding: 8px 16px 12px; gap: 10px; } .dictionary-tools input { width: 200px; } }
</style>
