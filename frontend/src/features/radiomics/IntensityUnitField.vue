<script setup lang="ts">
import { ref,watch } from 'vue'
import api from '@/api/client'
import type { RadiomicsPlanDraft } from './plan'
const props=defineProps<{draft:RadiomicsPlanDraft;filter:{studyId:number|null;taskId:number|null};sourceVersion:'legacy'|'physical-v1'}>()
const emit=defineEmits<{change:[patch:Partial<RadiomicsPlanDraft>]}>()
const loading=ref(false),matched=ref(false),message=ref(''),revision=ref(0)
function manual(event:Event){const value=(event.target as HTMLInputElement).value;const unknown=['unknown','未知'].includes(value.trim().toLowerCase());emit('change',{intensityKind:unknown?'unknown':'arbitrary',intensityUnit:unknown?'unknown':value,deviationAcknowledged:false})}
watch([()=>props.draft.resegmentation,()=>props.filter.taskId,()=>props.sourceVersion,()=>revision.value],async(_,__,cleanup)=>{let current=true;cleanup(()=>{current=false});matched.value=false;loading.value=false;message.value='';if(!props.draft.resegmentation||!props.filter.taskId)return;loading.value=true;emit('change',{intensityKind:'unknown',intensityUnit:''});try{const r:any=await api.post('/results/'+props.filter.taskId+'/feature-calculation/inputs',{source_version:props.sourceVersion});if(!current)return;if(r.intensity?.unit){matched.value=true;emit('change',{intensityKind:'calibrated',intensityUnit:r.intensity.unit});message.value='已按源文件核验单位'}else message.value='源文件无法确认单位，可人工声明或重新检测'}catch{if(current)message.value='单位检测失败，可重试或人工声明'}finally{if(current)loading.value=false}},{immediate:true})
</script>
<template><label class="unit-field"><span>阈值单位</span><input :value="draft.intensityUnit==='unknown'?'未知':draft.intensityUnit" :readonly="matched" :disabled="loading" placeholder="例如 HU、a.u. 或未知" @input="manual" /></label><p v-if="draft.resegmentation" class="unit-note" role="status">{{ loading?'正在核验源影像单位…':message }} <button v-if="!loading" type="button" class="c-link" @click="revision++">重新检测</button></p></template>
<style scoped>.unit-field{display:grid;gap:7px;font-size:12px;color:var(--c-muted)}input{width:100%;min-height:36px;padding:7px 10px;border:1px solid var(--c-line);border-radius:7px;background:var(--c-panel);color:var(--c-text)}.unit-note{margin-top:8px!important;font-size:12px;line-height:1.7;color:var(--c-muted)}</style>
