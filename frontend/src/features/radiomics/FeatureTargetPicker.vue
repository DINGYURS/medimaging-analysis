<script setup lang="ts">
import { computed, ref } from 'vue'
import FeatureDictionary from './FeatureDictionary.vue'
import FeatureFormula from './FeatureFormula.vue'
import documentation from './ibsiFeatureInfo.json'
import { featureFamilies, planDependencies } from './plan'
import type { RadiomicsFeatureDefinition } from './types'
const props=defineProps<{modelValue:string[];catalog:RadiomicsFeatureDefinition[]}>()
const emit=defineEmits<{'update:modelValue':[value:string[]]}>()
const dictionary=ref<InstanceType<typeof FeatureDictionary>|null>(null)
const featureInfo=documentation as Record<string,{name:string;code:string;description:string;formula:string}>
function info(feature:RadiomicsFeatureDefinition){return featureInfo[feature.key]}
const selected=computed(()=>new Set(props.modelValue))
const dependencies=computed(()=>planDependencies(props.modelValue,props.catalog))
const families=computed(()=>featureFamilies.map(f=>({...f,features:props.catalog.filter(x=>x.family===f.id),available:props.catalog.filter(x=>x.family===f.id&&x.status==='computed')})))
function toggle(keys:string[],checked:boolean){const next=new Set(props.modelValue);for(const key of keys)checked?next.add(key):next.delete(key);emit('update:modelValue',[...next])}
function familyChecked(keys:string[]){return keys.length>0&&keys.every(k=>selected.value.has(k))}
function setAll(checked:boolean){toggle(props.catalog.filter(f=>f.status==='computed').map(f=>f.key),checked)}
</script>
<template>
 <div class="target-actions c-between"><div class="c-row"><button type="button" class="c-button" @click="setAll(true)">选择目录中可计算项</button><button type="button" class="c-button" @click="setAll(false)">清空选择</button><button type="button" class="c-button dictionary-button" @click="dictionary?.open()">特征字典</button></div><span class="c-badge active" role="status">已选 {{ dependencies.count }} / 198 项 · {{ dependencies.families.length }} 类</span></div>
 <div class="c-family-grid target-grid">
  <article v-for="family in families" :key="family.id" class="target-card" :class="{selected:family.available.some(f=>selected.has(f.key))}">
   <label class="target-family"><input type="checkbox" :aria-label="family.label" :checked="familyChecked(family.available.map(f=>f.key))" :indeterminate="!familyChecked(family.available.map(f=>f.key))&&family.available.some(f=>selected.has(f.key))" @change="toggle(family.available.map(f=>f.key),($event.target as HTMLInputElement).checked)" /><span><strong>{{ family.label }}</strong><small>{{ family.short }}</small></span><span class="target-count">{{ family.available.filter(f=>selected.has(f.key)).length }} / {{ family.available.length }}</span></label>
   <details><summary>具体特征与依赖 <span v-if="family.features.length>family.available.length">（含 {{ family.features.length-family.available.length }} 项不可选说明）</span></summary><div class="target-feature-list">
    <div v-for="f in family.features" :key="f.key" class="target-feature" :data-feature-key="f.key"><input type="checkbox" :checked="selected.has(f.key)" :disabled="f.status!=='computed'" :aria-label="f.name+' '+f.key" @change="toggle([f.key],($event.target as HTMLInputElement).checked)" /><span><button type="button" class="feature-link" :aria-label="'在特征字典中查看 '+(info(f)?.name??f.name)" @click="dictionary?.open(f.key)"><strong>{{ info(f)?.name??f.name }} <code v-if="f.standard_status!=='custom'">{{ info(f)?.code??f.code }}</code></strong></button><small class="feature-description">{{ info(f)?.description??f.definition }}</small><small>输入：{{ f.required_masks.map(m=>m==='morphological'?'形态学掩膜':'强度掩膜').join('＋') }} · {{ f.requires_discretisation?'需离散化（'+f.discretisation_group+'）':'无需离散化' }}</small><FeatureFormula v-if="info(f)" :formula="info(f)!.formula" :show-label="false" /><small v-if="f.status!=='computed'" class="target-warning">{{ f.reason||'目录标记为不可计算' }}</small></span></div>
   </div></details>
  </article>
 </div>
 <div class="target-dependencies c-row" aria-live="polite"><span class="c-muted">自动依赖</span><span>{{ dependencies.morphology?'形态学掩膜':'保留原始 ROI' }}</span><span>{{ dependencies.intensity?'强度掩膜':'无需强度输入' }}</span><span>{{ dependencies.discretisation?'按特征族离散化':'无需离散化' }}</span><span v-if="dependencies.localContext">保留 ROI 外邻域</span></div>
 <FeatureDictionary ref="dictionary" :catalog="catalog" />
</template>
<style scoped>
.target-actions{margin-bottom:16px}.target-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.target-card{min-width:0;border:1px solid var(--c-line);border-radius:9px;background:var(--c-panel)}.target-card.selected{border-color:color-mix(in srgb,var(--c-blue) 40%,var(--c-line));background:color-mix(in srgb,var(--c-blue-soft) 22%,var(--c-panel))}.target-family{display:flex;align-items:flex-start;gap:9px;padding:15px 13px;cursor:pointer}.target-family>span:nth-child(2){flex:1;min-width:0}.target-family strong{font-size:13px}.target-family small{display:block;font-size:11px;color:var(--c-muted);margin-top:4px}.target-count{font-size:11px;white-space:nowrap;color:var(--c-blue);font-variant-numeric:tabular-nums}.target-card input{width:16px;height:16px;flex:0 0 16px;accent-color:var(--c-blue);margin:3px 0 0}.target-card summary{padding:10px 13px;border-top:1px solid var(--c-line);font-size:11px;color:var(--c-blue);cursor:pointer}.target-feature-list{max-height:330px;overflow:auto;padding:0 13px}.target-feature{display:flex;gap:9px;padding:12px 0;border-top:1px solid var(--c-line);font-size:12px;cursor:pointer}.target-feature>span{min-width:0;width:100%;overflow-wrap:anywhere}.target-feature small{display:block;margin-top:5px;color:var(--c-muted);line-height:1.6}.target-feature code{font-size:10px;color:var(--c-blue)}.target-feature small.target-warning{color:var(--c-amber)}.target-dependencies{margin-top:16px;padding:12px;border-radius:7px;background:var(--c-soft);font-size:12px}.target-dependencies>span:not(:first-child){display:inline-flex;align-items:center;min-height:30px;padding:5px 10px;border:1px solid var(--c-line);border-radius:6px;background:var(--c-panel);color:var(--c-text);line-height:1.5;cursor:default}@media(max-width:1000px){.target-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.target-grid{grid-template-columns:1fr}}
.feature-link{display:inline;padding:0;border:0;border-radius:2px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}.feature-link:hover{color:var(--c-blue);text-decoration:underline;text-underline-offset:3px}.feature-link:focus-visible{outline:2px solid var(--c-blue);outline-offset:3px}.dictionary-button{color:var(--c-blue)}
</style>
