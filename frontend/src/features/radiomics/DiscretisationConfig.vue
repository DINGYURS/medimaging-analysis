<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { RadiomicsConfig, RadiomicsDiscretisation } from './types'

type Rules = RadiomicsDiscretisation
type Group = keyof Rules
const props = defineProps<{ modelValue?: Rules | null; fallbackWidth: number | null; histogram: boolean; texture: boolean; ivh: boolean; sourceVersion?: RadiomicsConfig['source_version'] }>()
const emit = defineEmits<{ 'update:modelValue': [value: Rules | undefined]; 'update:fallbackWidth': [value: number | null] }>()
const groups = computed(() => [
  { key: 'intensity_histogram' as const, label: 'IH', active: props.histogram },
  { key: 'texture' as const, label: 'Texture', active: props.texture },
  { key: 'intensity_volume_histogram' as const, label: 'IVH', active: props.ivh },
])
const editor = () => ({ method: 'fbs', width: props.fallbackWidth ?? '' as number | string, count: '' as number | string, explicit: false, origin: '' as number | string })
const drafts = reactive({ intensity_histogram: editor(), texture: editor(), intensity_volume_histogram: { ...editor(), method: 'auto' } })
watch(() => props.modelValue, value => {
  if (!value) return
  for (const { key, active } of groups.value) {
    if (!active) continue
    const rule = value[key]
    if (rule.method === 'none') continue
    drafts[key].method = rule.method
    if (rule.method === 'fbn') drafts[key].count = Number.isFinite(rule.bin_count) ? rule.bin_count : ''
    if (rule.method === 'fbs') {
      drafts[key].width = Number.isFinite(rule.bin_width) ? rule.bin_width : ''
      drafts[key].explicit = rule.bin_origin != null
      if (rule.bin_origin != null) drafts[key].origin = Number.isFinite(rule.bin_origin) ? rule.bin_origin : ''
    }
  }
}, { immediate: true, deep: true })
const numeric = (value: number | string) => value === '' ? NaN : Number(value)
function rule(key: Group): Rules[Group] {
  const draft = drafts[key]
  if (draft.method === 'auto') return { method: 'auto' }
  if (draft.method === 'fbn') return { method: 'fbn', bin_count: numeric(draft.count) }
  return { method: 'fbs', bin_width: numeric(draft.width), bin_origin: draft.explicit ? numeric(draft.origin) : null }
}
function publish() {
  emit('update:modelValue', {
    intensity_histogram: props.histogram ? rule('intensity_histogram') as Rules['intensity_histogram'] : { method: 'none' },
    texture: props.texture ? rule('texture') as Rules['texture'] : { method: 'none' },
    intensity_volume_histogram: props.ivh ? rule('intensity_volume_histogram') as Rules['intensity_volume_histogram'] : { method: 'auto' },
  })
}
function toggle(event: Event) {
  if ((event.target as HTMLInputElement).checked) publish()
  else emit('update:modelValue', undefined)
}
watch(() => [props.histogram, props.texture, props.ivh], () => { if (props.modelValue) publish() })
</script>

<template>
  <div class="discretisation-config">
    <label class="check-row"><input type="checkbox" :checked="!!modelValue" @change="toggle" />分别配置 IH、Texture 和 IVH</label>
    <p v-if="!modelValue" class="hint">沿用历史离散化规则。IH 和纹理使用兼容 FBS；IVH 保留独立 Auto 规则</p>
    <template v-if="modelValue">
      <fieldset v-for="group in groups" v-show="group.active" :key="group.key" class="binning-group" :data-group="group.key" :aria-label="group.label">
        <h4>{{ group.label }}</h4>
        <fieldset class="radio-group"><legend>离散化方法</legend>
          <div class="radio-options">
            <label v-if="group.key === 'intensity_volume_histogram'"><input v-model="drafts[group.key].method" type="radio" :name="'binning-method-' + group.key" value="auto" @change="publish" /><span>Auto</span></label>
            <label><input v-model="drafts[group.key].method" type="radio" :name="'binning-method-' + group.key" value="fbs" @change="publish" /><span>Fixed Bin Size</span></label>
            <label v-if="group.key !== 'intensity_volume_histogram'"><input v-model="drafts[group.key].method" type="radio" :name="'binning-method-' + group.key" value="fbn" @change="publish" /><span>Fixed Bin Number</span></label>
          </div>
        </fieldset>
        <template v-if="group.key === 'intensity_volume_histogram' && drafts[group.key].method === 'auto'">
          <label class="field inline-number"><span>IVH Auto模式回退宽度</span><input type="number" min="0.000001" step="any" placeholder="自动：CT 25，MR 5" :value="fallbackWidth ?? ''" @input="emit('update:fallbackWidth', ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value))" /></label>
          <p class="hint">{{ sourceVersion === 'physical-v1' ? '未归一化且源文件声明 HU 时，使用 1 HU 强度间隔' : '未归一化 CT 使用 1 HU 强度间隔' }}；其他输入采用 Fixed Bin Size，使用上述回退宽度</p>
        </template>
        <label v-if="drafts[group.key].method === 'fbn'" class="field inline-number"><span>分箱数量</span><input v-model="drafts[group.key].count" type="number" min="1" step="1" @input="publish" /></label>
        <template v-if="drafts[group.key].method === 'fbs'">
          <label class="field inline-number"><span>分箱宽度</span><input v-model="drafts[group.key].width" type="number" min="0" step="any" @input="publish" /></label>
          <div class="origin-row" :class="{ 'with-origin': drafts[group.key].explicit }">
            <fieldset class="radio-group origin-options"><legend>固定宽度分箱起点</legend>
              <div class="radio-options">
                <label><input v-model="drafts[group.key].explicit" type="radio" :name="'binning-origin-' + group.key" :value="false" @change="publish" /><span>当前强度 ROI 最小值</span></label>
                <label><input v-model="drafts[group.key].explicit" type="radio" :name="'binning-origin-' + group.key" :value="true" @change="publish" /><span>显式指定</span></label>
              </div>
            </fieldset>
            <label v-if="drafts[group.key].explicit" class="field origin-value"><span>显式起点</span><input v-model="drafts[group.key].origin" type="number" step="any" @input="publish" /></label>
          </div>
        </template>
      </fieldset>
    </template>
  </div>
</template>

<style scoped>
.binning-group{margin:0;padding:14px 0;min-width:0;border:0;border-radius:0;background:transparent}.binning-group+.binning-group{border-top:1px solid var(--c-line)}.binning-group:last-child{padding-bottom:0}h4{font-size:13px;font-weight:600;margin:0 0 10px;color:var(--c-text)}.field{display:grid;gap:6px;margin:0 0 8px;min-width:0;font-size:12px;color:var(--c-muted)}input:not([type=checkbox]):not([type=radio]){box-sizing:border-box;width:100%;min-width:0;min-height:36px;padding:7px 9px;border:1px solid var(--c-line);border-radius:7px;font:inherit;color:var(--c-text);background:var(--c-panel)}.radio-group{border:0;min-width:0;margin:0 0 8px;padding:0}.radio-group legend{font-size:12px;color:var(--c-muted);margin-bottom:4px}.radio-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 14px;align-items:start}.radio-options label{min-width:0;align-items:flex-start;line-height:20px;padding-top:0}.radio-options label input{margin-top:2px}.radio-options label span{min-width:0;overflow-wrap:anywhere}.radio-options label,.check-row{display:flex;gap:6px;align-items:center;min-height:30px;font-size:12px;color:var(--c-text);cursor:pointer}input[type=checkbox],input[type=radio]{flex:0 0 16px;width:16px;height:16px;margin:0;accent-color:var(--c-blue)}.inline-number{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}.field.inline-number>input{width:180px;max-width:100%;flex:0 1 180px}.origin-row{display:grid;grid-template-columns:1fr;gap:8px}.origin-options{margin-bottom:0}.origin-value{max-width:160px;margin-bottom:0}.hint{color:var(--c-muted);font-size:12px;line-height:1.7;margin-top:6px!important}
@container(max-width:260px){.radio-options{grid-template-columns:1fr}}
</style>
