<template>
  <div :class="{ 'c-result-block': !currentDraft }" :aria-busy="busy">
    <div v-if="!currentDraft" class="c-between"><h2>{{ verified ? '专项影像洞察' : '分析结果' }}</h2><span class="c-badge neutral" role="status">{{ statusLabel }}</span></div>
    <p v-if="!currentDraft" class="c-small c-muted" style="margin:12px 0 16px">{{ verified ? '展示通过门限的专项影像模型结果，供阅片参考。' : '基于候选病灶的结构化测量，供阅片参考。' }}</p>
    <p v-if="loadError" class="c-alert" role="alert">{{ loadError }} <button class="c-link" @click="refresh">重新读取</button></p>
    <p v-if="data?.message" class="c-small" role="status" style="margin-bottom:12px">{{ data.message }}</p>
    <p v-if="data?.is_stale" class="c-alert" role="status">依据数据已更新，可查看此前生成的内容。</p>
    <CTReportPreviewPanel v-if="currentDraft" :report="currentDraft" :generation-status="statusLabel"
      @locate="(id, anchor) => $emit('locate', id, anchor)" @locate-trial="anchor => $emit('locateTrial', anchor)">
      <template #actions>
        <button v-if="canGenerate" type="button" class="c-button report-regenerate"
                :aria-label="generateLabel" :disabled="busy" @click="generate">重新生成</button>
      </template>
    </CTReportPreviewPanel>
    <component v-if="!currentDraft && data?.result" :is="data.is_previous ? 'details' : 'div'" :key="historyKey"
      :class="{ 'c-insight-section': data.is_previous }">
      <summary v-if="data.is_previous">上一次成功结果</summary>
        <section class="c-insight-section"><h3>结果摘要</h3><p style="white-space:pre-wrap">{{ data.result.result_summary }}</p></section>
        <section class="c-insight-section"><h3>{{ verified ? '病灶结果' : '定量解读' }}</h3>
          <div v-for="[lesionId, items] in lesionGroups" :key="lesionId" style="margin-top:12px">
            <button class="c-link" @click="$emit('locate',lesionId)">病灶 #{{ lesionId.replace(/^L/,'') }}</button>
            <div v-for="(item,index) in items" :key="index" style="margin-top:6px">
              <p style="white-space:pre-wrap">{{ item.text }}</p>
              <button v-for="evidence in locations(item)" :key="evidence.evidence_id" class="c-link"
                @click="$emit('locate',lesionId,evidence.anchor)">定位毛刺候选区域 · 第 {{ evidence.anchor?.slice }} 层</button>
              <details v-if="item.evidence_ids.length" class="c-small c-muted" style="margin-top:6px">
                <summary>{{ verified ? '模型依据' : '指标依据' }}</summary>
                <p v-for="id in item.evidence_ids" :key="id">{{ evidenceText(id) }}</p>
              </details>
            </div>
          </div>
          <p v-if="!data.result.quantitative_interpretation.length" class="c-muted">{{ verified ? '当前资料没有达到展示门限的病灶结果。' : '当前资料没有可进一步解读的定量指标。' }}</p>
        </section>
        <details v-if="specialistHints.length" class="c-insight-section">
          <summary>专项模型提示</summary>
          <p v-for="hint in specialistHints" :key="hint.evidence_id" class="c-small">
            {{ hint.lesion_id }} · {{ hint.label }} · {{ hint.value }}（{{ hint.model }}）
            <button v-if="hint.anchor && hint.lesion_id" class="c-link"
              @click="$emit('locate', hint.lesion_id, hint.anchor)">查看第 {{ hint.anchor.slice }} 层</button>
          </p>
        </details>
        <p v-if="data.finished_at" class="c-small c-muted">{{ displayTime(data.finished_at) }}</p>
    </component>
    <div v-else-if="!currentDraft && !data?.message" class="c-insight-section c-muted" style="padding-bottom:18px">{{ emptyText }}</div>
    <details v-if="data?.chest_model_trial && !data.clinical_report_preview?.whole_chest_research.length
      && !data.historical_clinical_report_preview?.whole_chest_research.length" class="c-insight-section">
      <summary>全胸模型试验</summary>
      <p class="c-small c-muted" style="margin-top:8px">本次仅验证推理和原片定位。以下为待验收的模型输出。</p>
      <section v-for="model in data.chest_model_trial.models" :key="model.id" style="margin-top:12px">
        <h3>{{ model.name }}</h3>
        <div v-for="finding in model.findings" :key="finding.label" class="c-small" style="margin-top:6px">
          <strong>{{ finding.label }}</strong>
          <span> · {{ finding.voxels ? finding.volume_ml + ' mL' : '模型输出为空' }}</span>
          <button v-if="finding.anchor" class="c-link" style="margin-left:8px"
            @click="$emit('locateTrial', finding.anchor)">
            定位{{ finding.label }}候选区 · 第 {{ finding.anchor.slice }} 层
          </button>
        </div>
      </section>
    </details>
    <button v-if="!currentDraft && canGenerate" class="c-button c-primary" style="margin-top:14px" :disabled="busy" @click="generate">{{ generateLabel }}</button>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import api from '@/api/client';
import CTReportPreviewPanel, { type CTReportPreview } from './CTReportPreview.vue';

interface Interpretation { lesion_id:string; text:string; evidence_ids:string[] }
type SpatialAnchor = {slice:number;box_xy:[number,number,number,number]};
interface ChestTrial {
  models:{id:string;name:string;findings:{label:string;voxels:number;volume_ml:number;anchor:SpatialAnchor|null}[]}[]
}
interface Insight { id?:number; prompt_version?:string; clinical_report_preview?:CTReportPreview; historical_clinical_report_preview?:CTReportPreview; chest_model_trial?:ChestTrial; status:string; message?:string; model?:string; finished_at?:string; is_stale?:boolean; is_previous?:boolean; evidence?:{evidence_id:string;lesion_id?:string;label:string;value:number|string;unit:string|null;model?:string;anchor?:SpatialAnchor}[]; result?:{schema_version?:string;result_summary:string;quantitative_interpretation:Interpretation[];limitations:string[];specialist_evidence_ids?:string[]}|null }
const props=defineProps<{taskId:number}>();
defineEmits<{locate:[id:string,anchor?:SpatialAnchor];locateTrial:[anchor:SpatialAnchor]}>();
const data=ref<Insight|null>(null),loadError=ref(''),submitting=ref(false);
let version=0,disposed=false,timer:ReturnType<typeof setTimeout>|null=null;
const verified=computed(()=>data.value?.result?.schema_version==='ct-verified-attributes-v1');
const currentDraft = computed(() => {
  const latest = data.value;
  const draft = latest?.clinical_report_preview;
  if (draft?.basis_insight_id === undefined || !latest || latest.is_previous || latest.is_stale ||
      latest.status !== 'SUCCEEDED' || latest.id !== draft.basis_insight_id) return undefined;
  return draft;
});
const historyKey = computed(() => [props.taskId, data.value?.id, data.value?.status].join(':'));
const lesionGroups = computed(() => {
  const groups = new Map<string, Interpretation[]>();
  for (const item of data.value?.result?.quantitative_interpretation || []) {
    const rows = groups.get(item.lesion_id);
    if (rows) rows.push(item);
    else groups.set(item.lesion_id, [item]);
  }
  return groups;
});
const specialistHints = computed(() => {
  const ids = new Set(data.value?.result?.specialist_evidence_ids);
  return (data.value?.evidence || []).filter(fact => ids.has(fact.evidence_id));
});
const generateLabel = computed(() => {
  const action = data.value?.status === 'FAILED' ? '重试' : data.value?.result ? '重新生成' : '生成';
  if (data.value?.prompt_version === 'ct-evidence-report-v1') return action + ' CT 报告';
  return action + (data.value?.prompt_version === 'ct-quantitative-insight-v2' ? '洞察与草稿' : ' AI 洞察');
});
function locations(item:Interpretation){return (data.value?.evidence||[]).filter(e=>item.evidence_ids.includes(e.evidence_id)&&e.anchor);}
const busy=computed(()=>submitting.value||['QUEUED','RUNNING'].includes(data.value?.status||''));
const statusLabel=computed(()=>({NOT_ENABLED:'等待启用',NOT_GENERATED:'尚未生成',WAITING_CONFIGURATION:'等待配置密钥',QUEUED:'等待生成',RUNNING:'正在生成',SUCCEEDED:data.value?.is_stale?'数据已更新':'已生成',FAILED:'生成失败',BLOCKED:'资料暂不可用',SUPERSEDED:'数据已更新'}[data.value?.status||'']||'正在读取'));
const canGenerate=computed(()=>!!data.value&&!['NOT_ENABLED','WAITING_CONFIGURATION','QUEUED','RUNNING'].includes(data.value.status));
const emptyText=computed(()=>({NOT_ENABLED:'AI 洞察配置完成后，新的分析任务将自动生成参考内容。',WAITING_CONFIGURATION:'等待服务器配置，影像分析结果仍可正常查看。',QUEUED:'任务已加入队列，可继续阅览影像。',RUNNING:'正在整理结果摘要和定量解读。',FAILED:'本次洞察未生成，可稍后重试。',BLOCKED:'当前资料暂不支持生成洞察。',SUPERSEDED:'依据数据已更新，可以重新生成。'}[data.value?.status||'']||'影像分析完成后自动生成，已有分析也可手动生成。'));
function displayTime(value:string){return new Date(value.endsWith('Z')?value:value+'Z').toLocaleString('zh-CN',{hour12:false});}
function evidenceText(id:string){const item=data.value?.evidence?.find(x=>x.evidence_id===id);if(!item)return '依据暂不可用';if(typeof item.value==='string')return item.label+'：'+item.value+(item.model?'（'+item.model+'）':'');const unit=item.unit==='dimensionless'?'（无量纲）':item.unit==='mm3'?'mm³':item.unit||'（单位未确认）';return item.label+'：'+item.value+' '+unit;}
function clearTimer(){if(timer)clearTimeout(timer);timer=null;}
async function fetchInsight(current:number){
 try{
  const result=await api.get('/insights/'+props.taskId) as unknown as Insight;
  if(disposed||current!==version)return;
  data.value=result;loadError.value='';
  if(['QUEUED','RUNNING','WAITING_CONFIGURATION'].includes(result.status))timer=setTimeout(()=>void fetchInsight(current),result.status==='WAITING_CONFIGURATION'?15000:3000);
 }catch{if(!disposed&&current===version)loadError.value='AI 洞察读取失败，请重试。';}
}
function refresh(){clearTimer();void fetchInsight(++version);}
async function generate(){
 if(busy.value)return;
 clearTimer();const current=++version;submitting.value=true;
 try{const result=await api.post('/insights/'+props.taskId+'/generate') as unknown as Insight;if(disposed||current!==version)return;data.value=result;loadError.value='';await fetchInsight(current);}
 catch{if(!disposed&&current===version)loadError.value='AI 洞察提交失败，请稍后重试。';}
 finally{if(current===version)submitting.value=false;}
}
watch(()=>props.taskId,()=>{data.value=null;loadError.value='';submitting.value=false;refresh();},{immediate:true});
onBeforeUnmount(()=>{disposed=true;version++;clearTimer();});
</script>

<style scoped>
.report-regenerate { min-height:32px; padding:4px 10px; white-space:nowrap; }
</style>
