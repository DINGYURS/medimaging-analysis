<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const props = defineProps<{ formula: string; showLabel?: boolean; copyable?: boolean }>()
const root = ref<HTMLElement | null>(null)
const copyState = ref<'idle' | 'copied' | 'failed'>('idle')
let resetCopy: ReturnType<typeof setTimeout> | undefined
async function copyLatex() {
  const latex = [...props.formula.matchAll(/\\\(([\s\S]*?)\\\)/g)].map(part => part[1]).join('\n')
  if (!latex) return
  let copied = false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(latex)
      copied = true
    }
  } catch { /* Try the user-initiated fallback for HTTP/clipboard restrictions. */ }
  if (!copied) {
    const previous = document.activeElement as HTMLElement | null
    const field = document.createElement('textarea')
    field.value = latex
    field.readOnly = true
    field.setAttribute('aria-label', '待复制的 LaTeX 公式')
    Object.assign(field.style, { position: 'fixed', left: '0', top: '0', opacity: '0', width: '1px', height: '1px' })
    // Keep the selection inside the modal's focus boundary.
    ;(root.value?.closest('dialog') ?? document.body).appendChild(field)
    try {
      field.focus()
      field.select()
      copied = document.execCommand('copy')
    } catch { copied = false }
    finally { field.remove(); previous?.focus({ preventScroll: true }) }
  }
  copyState.value = copied ? 'copied' : 'failed'
  clearTimeout(resetCopy)
  resetCopy = setTimeout(() => { copyState.value = 'idle' }, 2400)
}
onBeforeUnmount(() => clearTimeout(resetCopy))

// Formula strings come from the checked-in IBSI document; trust stays disabled.
// Render each inline LaTeX fragment separately so explanatory text is preserved.
const parts = computed(() => props.formula.split(/\\\(([\s\S]*?)\\\)/g).map((value, index) => ({
  text: index % 2 === 0 ? value : '',
  html: index % 2 === 1 ? katex.renderToString(value, {
    displayMode: false, throwOnError: false, trust: false, strict: 'ignore',
    output: 'htmlAndMathml',
  }) : '',
})))
</script>

<template>
  <div ref="root" class="feature-formula">
    <small v-if="showLabel !== false" class="formula-label">计算公式：</small>
    <component :is="copyable ? 'button' : 'div'" class="formula-content" :class="{ 'formula-copy': copyable }"
      :type="copyable ? 'button' : undefined" :role="copyable ? undefined : 'region'"
      :aria-label="copyable ? '复制公式为 LaTeX' : '计算公式'" :title="copyable ? '点击复制 LaTeX 公式' : undefined"
      tabindex="0" @click="copyable && copyLatex()">
      <template v-for="(part, index) in parts" :key="index">
        <span v-if="part.html" v-html="part.html"></span>
        <span v-else>{{ part.text }}</span>
      </template>
    </component>
    <small v-if="copyable && copyState !== 'idle'" class="copy-status" :class="{ 'copy-failed': copyState === 'failed' }" role="status">{{ copyState === 'copied' ? '已复制 LaTeX' : '复制失败，请检查浏览器剪贴板权限' }}</small>
  </div>
</template>

<style scoped>
.feature-formula { margin-top: 8px; color: var(--c-muted); font-size: 12px; line-height: 1.6; }
.formula-label { font-size: smaller; display: block; margin-bottom: 4px; color: var(--c-muted); }
.formula-content { max-width: 100%; overflow-x: auto; padding: 5px 2px 9px; }
.formula-copy { display: block; width: 100%; border: 0; border-radius: 4px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: copy; }
.copy-status { display: block; margin-top: 4px; color: var(--c-blue); font-size: 13px; }
.copy-failed { color: var(--c-red); }
.formula-content:focus-visible { outline: 2px solid var(--c-blue); outline-offset: 2px; border-radius: 3px; }
.formula-content :deep(.katex) { font-size: 1.08em; white-space: nowrap; }
</style>
