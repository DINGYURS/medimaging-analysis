<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ClinicalSelect from '@/components/ClinicalSelect.vue'
import type { RadiomicsConfig, RadiomicsFeatureDefinition } from './types'
import { planDependencies, type RadiomicsPlanDraft } from './plan'
import DiscretisationConfig from './DiscretisationConfig.vue'
import IntensityUnitField from './IntensityUnitField.vue'

const props = defineProps<{ modelValue: RadiomicsConfig; draft: RadiomicsPlanDraft; modalities: string[]; catalog: RadiomicsFeatureDefinition[]; scopeFilter: { studyId: number | null; taskId: number | null }; collapsed?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: RadiomicsConfig]; 'update:draft': [value: RadiomicsPlanDraft]; 'update:collapsed': [value: boolean] }>()
const dependencies = computed(() => planDependencies(props.draft.selectedKeys, props.catalog))
const native = computed(() => props.modelValue.resampling === 'none')
const lastSpacing = ref<[number, number, number]>(props.modelValue.resampling === 'none' ? [1, 1, 1] : [...props.modelValue.resampling])
watch(() => props.modelValue.resampling, value => {
  if (value !== 'none') lastSpacing.value = [...value]
}, { deep: true })
function setGridMode(mode: 'native' | 'resampled') {
  update({ resampling: mode === 'native' ? 'none' : [...lastSpacing.value] })
}
function update(patch: Partial<RadiomicsConfig>) { emit('update:modelValue', { ...props.modelValue, ...patch }) }
function updateDraft(patch: Partial<RadiomicsPlanDraft>) { emit('update:draft', { ...props.draft, ...patch }) }
function setSpacing(axis: number, value: string) {
  const next: [number, number, number] = props.modelValue.resampling === 'none' ? [1, 1, 1] : [...props.modelValue.resampling]
  next[axis] = value === '' ? NaN : Number(value)
  update({ resampling: next })
}
const displayNumber = (value: number | string) => typeof value === 'number' && Number.isFinite(value) ? value : ''
const numberValue = (event: Event) => (event.target as HTMLInputElement).value
</script>

<template>
  <div class="processing-form">
    <div id="processing-settings-content" v-show="!collapsed">
    <p class="hint" v-if="!dependencies.count">请先在 02 目标特征中选择需要计算的特征</p>
    <div class="settings-grid" v-show="dependencies.count > 0">
      <section class="settings-panel">
        <h3>重采样与插值</h3>
        <label class="field"><span>源影像读取版本</span><ClinicalSelect :model-value="modelValue.source_version ?? 'legacy'" aria-label="源影像读取版本" :options="[{value:'physical-v1',label:'按源文件读取空间信息与强度',description:'核验源空间、统一为 mm，支持逐帧强度转换'},{value:'legacy',label:'历史兼容读取',description:'沿用旧空间与强度读取规则，便于复现历史结果'}]" @update:model-value="update({source_version:$event as RadiomicsConfig['source_version']})" /></label>
        <fieldset class="control-group radio-group grid-mode"><legend>分析网格</legend>
          <div class="radio-options">
            <label><input type="radio" name="analysis-grid" value="resampled" :checked="!native" @change="setGridMode('resampled')" /><span>自定义目标间距</span></label>
            <label><input type="radio" name="analysis-grid" value="native" :checked="native" @change="setGridMode('native')" /><span>保留原生网格</span></label>
          </div>
        </fieldset>
        <div v-if="!native" class="resampling-settings">
          <fieldset class="control-group"><legend>目标体素间距（mm）</legend>
            <div class="spacing-fields"><label v-for="(axis, index) in ['X', 'Y', 'Z']" :key="axis" class="field spacing-axis"><span>{{ axis }}</span><input type="number" min="0.000001" step="any" :value="displayNumber(native ? lastSpacing[index] : modelValue.resampling[index])" @input="setSpacing(index, numberValue($event))" /></label></div>
          </fieldset>
          <fieldset class="control-group radio-group"><legend>影像插值方式</legend>
            <div class="radio-options">
              <label><input type="radio" name="image-interpolator" value="sitkBSpline" :checked="(modelValue.image_interpolator ?? 'sitkBSpline') === 'sitkBSpline'" @change="update({ image_interpolator: 'sitkBSpline' })" /><span>B-Spline</span></label>
              <label><input type="radio" name="image-interpolator" value="sitkLinear" :checked="modelValue.image_interpolator === 'sitkLinear'" @change="update({ image_interpolator: 'sitkLinear' })" /><span>Trilinear</span></label>
              <label><input type="radio" name="image-interpolator" value="sitkNearestNeighbor" :checked="modelValue.image_interpolator === 'sitkNearestNeighbor'" @change="update({ image_interpolator: 'sitkNearestNeighbor' })" /><span>Nearest Neighbor</span></label>
            </div>
          </fieldset>
          <div class="roi-interpolation-row" :class="{ 'with-threshold': draft.maskInterpolator === 'sitkLinear' }">
          <fieldset class="control-group radio-group roi-mode"><legend>ROI插值方式</legend>
            <div class="radio-options">
              <label><input type="radio" name="roi-interpolator" value="sitkNearestNeighbor" :checked="draft.maskInterpolator === 'sitkNearestNeighbor'" @change="updateDraft({ maskInterpolator: 'sitkNearestNeighbor' })" /><span>Nearest Neighbor</span></label>
              <label><input type="radio" name="roi-interpolator" value="sitkLinear" :checked="draft.maskInterpolator === 'sitkLinear'" @change="updateDraft({ maskInterpolator: 'sitkLinear' })" /><span>Trilinear (with Threshold)</span></label>
            </div>
          </fieldset>
          <label v-if="draft.maskInterpolator === 'sitkLinear'" class="field threshold-field"><span>掩膜二值化阈值</span><input type="number" min="0.000001" max="1" step="any" :value="draft.maskThreshold" @input="updateDraft({ maskThreshold: numberValue($event) })" /></label>
          </div>
        </div>
      </section>

      <section v-show="dependencies.intensity" class="settings-panel resegmentation-panel">
        <h3>重分割</h3>
        <label class="check-row"><input type="checkbox" :checked="draft.resegmentation" @change="updateDraft({ resegmentation: ($event.target as HTMLInputElement).checked })" /><span>启用固定强度范围筛选</span></label>
        <fieldset :disabled="!draft.resegmentation" class="control-group"><legend>保留范围（含边界）</legend><div class="range-fields">
          <label class="field"><span>最低强度</span><input type="number" step="any" :value="draft.intensityMin" placeholder="输入下限" @input="updateDraft({ intensityMin: numberValue($event) })" /></label>
          <label class="field"><span>最高强度</span><input type="number" step="any" :value="draft.intensityMax" placeholder="输入上限" @input="updateDraft({ intensityMax: numberValue($event) })" /></label>
        </div></fieldset>
        <fieldset :disabled="!draft.resegmentation" class="control-group"><legend>归一化前强度的单位与来源</legend>
          <IntensityUnitField :draft="draft" :filter="scopeFilter" :source-version="modelValue.source_version ?? 'legacy'" @change="updateDraft" />
        </fieldset>
        <p v-if="draft.resegmentation && (modelValue.include_liver_features ?? true)" class="warning-text">请关闭肝脏补充特征后再保存或提交</p>
      </section>

      <section v-show="dependencies.intensity" class="settings-panel">
        <h3>影像强度归一化</h3>
        <fieldset class="control-group radio-group grid-mode"><legend>处理顺序</legend>
          <div class="radio-options">
            <label><input type="radio" name="normalization-order" :checked="(modelValue.processing_order ?? 'legacy') === 'legacy'" @change="update({ processing_order: 'legacy' })" /><span>在插值前归一化</span></label>
            <label><input type="radio" name="normalization-order" :checked="modelValue.processing_order === 'interpolate_then_normalize'" @change="update({ processing_order: 'interpolate_then_normalize' })" /><span>在插值后归一化</span></label>
          </div>
        </fieldset>
        <p v-if="draft.resegmentation && (modelValue.processing_order ?? 'legacy') === 'legacy'" class="hint">启用重分割时，归一化在插值及重分割后执行。</p>
        <fieldset class="control-group radio-group"><legend>归一化策略</legend>
          <div class="radio-options">
            <label><input type="radio" name="normalization-method" :checked="modelValue.normalization === 'none'" @change="update({ normalization: 'none' })" /><span>不归一化</span></label>
            <label><input type="radio" name="normalization-method" :checked="modelValue.normalization === 'z-score'" @change="update({ normalization: 'z-score' })" /><span>Z-Score</span></label>
            <label><input type="radio" name="normalization-method" :checked="modelValue.normalization === 'auto'" @change="update({ normalization: 'auto' })" /><span>按模态自适应</span></label>
          </div>
        </fieldset>
        <label v-if="modelValue.normalization !== 'none'" class="field inline-number normalization-scale"><span>归一化倍率</span><input type="number" min="0.000001" max="10000" step="any" :value="modelValue.normalization_scale ?? 100" @input="update({ normalization_scale: Number(numberValue($event)) })" /></label>
      </section>

      <section v-show="dependencies.discretisation" class="settings-panel">
        <h3>灰度离散化</h3>
        <fieldset class="control-group"><legend>按目标特征的依赖配置</legend>
          <DiscretisationConfig :model-value="modelValue.discretisation" :source-version="modelValue.source_version" :fallback-width="modelValue.bin_width" :histogram="dependencies.histogram" :texture="dependencies.texture" :ivh="dependencies.ivh" @update:model-value="update({ discretisation: $event })" @update:fallback-width="update({ bin_width: $event })" />
          <label v-show="!modelValue.discretisation" class="field inline-number"><span>兼容模式FBS</span><input type="number" min="0.000001" step="any" placeholder="自动：CT 25，MR 5" :value="modelValue.bin_width ?? ''" @input="update({ bin_width: numberValue($event) === '' ? null : Number(numberValue($event)) })" /></label>
        </fieldset>
      </section>

    </div>
    <label v-show="dependencies.count > 0" class="check-row"><input type="checkbox" :checked="modelValue.include_liver_features ?? false" @change="update({ include_liver_features: ($event.target as HTMLInputElement).checked })" /><span>适用模态追加肝脏补充特征</span></label>
    </div>
  </div>
</template>

<style scoped>
.processing-form{font-size:13px}.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 32px;align-items:start}.settings-panel{container-type:inline-size;padding:4px 0 0;min-width:0;border:0;border-radius:0;background:transparent}.settings-panel:nth-child(n+3){padding-top:20px;border-top:1px solid var(--c-line)}h3{font-size:14px!important;margin:0 0 16px!important;color:var(--c-text)}.field{display:grid;gap:7px;margin:0 0 14px;color:var(--c-muted);font-size:12px;min-width:0}.control-group{min-width:0;margin:0 0 14px;padding:0;border:0}.control-group legend{margin-bottom:7px;color:var(--c-muted);font-size:12px}input:not([type=checkbox]):not([type=radio]),select{box-sizing:border-box;width:100%;min-width:0;min-height:36px;padding:7px 10px;border:1px solid var(--c-line);border-radius:7px;background:var(--c-panel);color:var(--c-text);font:inherit}input:focus-visible,select:focus-visible{outline:2px solid var(--c-blue);outline-offset:2px}input[type=radio],input[type=checkbox]{width:16px;height:16px;flex:0 0 16px;margin:0;accent-color:var(--c-blue)}.radio-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px 16px;align-items:start}.radio-options label{min-width:0;align-items:flex-start;line-height:20px;padding-top:8px}.radio-options label input{margin-top:2px}.radio-options label span{min-width:0;overflow-wrap:anywhere}.roi-mode .radio-options>label:last-child{grid-column:2/-1}.radio-options label,.check-row{display:flex;gap:7px;align-items:center;min-height:36px;font-size:12px;color:var(--c-text);cursor:pointer}.spacing-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.spacing-axis{display:flex;align-items:center;gap:6px}.spacing-axis input{min-width:0}.range-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.roi-interpolation-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 16px}.roi-mode,.with-threshold .roi-mode{grid-column:1/-1}.threshold-field{grid-column:2/-1;max-width:160px}.inline-number{grid-template-columns:max-content minmax(80px,160px);align-items:center}.check-row{margin-top:10px}.hint{color:var(--c-muted);font-size:12px;line-height:1.7;margin:10px 0!important}.warning-text{color:var(--c-amber);font-size:12px}input:disabled,select:disabled{opacity:.55;cursor:not-allowed;background:var(--c-soft)}@media(max-width:800px){.settings-grid{grid-template-columns:1fr;gap:20px}.settings-panel+.settings-panel{padding-top:20px;border-top:1px solid var(--c-line)}}@container(max-width:420px){.radio-options,.roi-interpolation-row{grid-template-columns:repeat(2,minmax(0,1fr))}}@container(max-width:260px){.radio-options,.roi-interpolation-row{grid-template-columns:1fr}.roi-mode .radio-options>label:last-child,.threshold-field{grid-column:1}.inline-number{grid-template-columns:1fr}}
</style>
