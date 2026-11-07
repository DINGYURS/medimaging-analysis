<template>
  <ClinicalDialog :model-value="modelValue" :title="current?.status==='FAILED'?'处理异常':isActive(current)?'分析进度':'检查详情与分析记录'" :subtitle="study?'检查 ID '+studyCode(study.id):''" drawer @update:model-value="emit('update:modelValue',$event)">
    <template v-if="study">
      <span class="c-badge" :class="statusTone(current)">{{ statusLabel(current) }}</span>
      <dl style="margin-top:14px"><div class="c-detail-row"><dt>检查信息</dt><dd>{{ study.modality || '—' }}</dd></div><div class="c-detail-row"><dt>切片层数</dt><dd>{{ numberText(study.slice_count,0,true) }}</dd></div><div v-if="studyDate(study.study_date)!=='—'" class="c-detail-row"><dt>检查日期</dt><dd>{{ studyDate(study.study_date) }}</dd></div><div class="c-detail-row"><dt>导入时间</dt><dd>{{ dateTime(study.created_at) }}</dd></div></dl>
      <p v-if="statusError" class="c-small c-muted" role="status" style="margin-top:14px">{{ statusError }}</p>
      <template v-if="isActive(current)"><h3 style="margin-top:23px">当前处理阶段</h3><div v-for="(step,index) in steps" :key="step.title" class="c-step" :class="{done:index<stage,current:index===stage}"><span class="c-step-number">{{ index<stage?'✓':index+1 }}</span><div><h3>{{ step.title }}</h3><p class="c-small c-muted">{{ step.description }}</p></div></div><p class="c-small c-muted">阶段状态由分析服务提供，未提供连续计算百分比。</p></template>
      <div v-if="current?.status==='FAILED'" class="c-alert" style="margin-top:20px"><h3>本次分析未完成</h3><p style="margin-top:6px">{{ failureReason }}</p></div>
      <h3 style="margin:23px 0 12px">分析记录</h3>
      <p v-if="tasks.length" class="c-muted c-small" style="margin-bottom:10px">点击记录编号，查看该次分析的影像与结果。</p>
      <p v-if="!tasks.length" class="c-muted c-small">尚无分析记录。</p>
      <div v-for="task in tasks" :key="taskId(task) || task.task_code" class="c-history">
        <div class="c-between"><button class="c-link" type="button" @click="emit('open',taskId(task))">{{ task.task_code || ('任务 #'+taskId(task)) }}</button><span class="c-badge" :class="statusTone(task)">{{ statusLabel(task) }}</span></div>
        <p class="c-subline">提交于 {{ dateTime(task.created_at) }}</p><p v-if="task.total_execution_time" class="c-subline">处理耗时 {{ numberText(task.total_execution_time,1,true) }} 秒</p>
      </div>
    </template>
    <template #footer><button class="c-button" @click="emit('open',selectedId)">阅览影像</button><button v-if="!current || current.status==='FAILED'" class="c-button c-primary" @click="emit('submit')">{{ current?'重新提交':'提交分析' }}</button></template>
  </ClinicalDialog>
</template>
<script setup lang="ts">
import { ref,computed,watch,onBeforeUnmount } from 'vue';
import ClinicalDialog from './ClinicalDialog.vue';
import api from '@/api/client';
import { sortedTasks,displayTask,taskId,isActive,statusLabel,statusTone,studyCode,studyDate,dateTime,numberText,type Study,type AnalysisTask } from '@/ui/clinical';
const props=defineProps<{modelValue:boolean;study:Study|null}>();
const emit=defineEmits<{'update:modelValue':[boolean];open:[number|null];submit:[]}>();
const selectedId=ref<number|null>(null), live=ref<AnalysisTask|null>(null),statusError=ref('');
const tasks=computed(()=>sortedTasks(props.study).map(task=>live.value&&taskId(task)===taskId(live.value)?{...task,...live.value}:task));
const current=computed(()=>live.value || tasks.value.find(t=>taskId(t)===selectedId.value) || displayTask(props.study));
const stage=computed(()=>current.value?.status==='QUEUED'?0:current.value?.status==='SEGMENTING'?1:current.value?.status==='EXTRACTING'?2:4);
const steps=[{title:'等待处理',description:'等待计算资源分配'},{title:'病灶分析',description:'执行模型识别与定量处理'},{title:'结果整理',description:'保存病灶指标与结构化结果'},{title:'分析完成',description:'结果可在工作台查看'}];
const failureReason=computed(()=>{
 const error=current.value?.error_message||'';
 if(/out of memory|CUDA.*memory/i.test(error))return '计算资源不足，请稍后重试或联系管理员。';
 if(/not found|不存在|未找到|FileNotFound/i.test(error))return '影像或结果文件不可用，请联系管理员核对输入数据。';
 if(/geometry|spacing|direction|尺寸|几何/i.test(error))return '影像与标注的空间信息不匹配，请核对输入序列。';
 return '分析执行失败，请联系管理员核对该任务的运行记录。原始影像仍可阅览。';
});
let timer:ReturnType<typeof setTimeout>|null=null;let generation=0;
async function refresh(){
 const version=generation,id=selectedId.value;if(!props.modelValue||!id)return;
 try{const result:any=await api.get('/tasks/'+id+'/status');if(version!==generation)return;live.value=result;statusError.value='';}
 catch{if(version===generation)statusError.value='状态更新暂不可用，可关闭后重新查看。';}
 if(version===generation&&props.modelValue&&isActive(current.value))timer=setTimeout(refresh,4000);
}
function selectTask(task:AnalysisTask){generation++;if(timer)clearTimeout(timer);selectedId.value=taskId(task);live.value=task;statusError.value='';void refresh();}
watch(()=>[props.modelValue,props.study?.id],()=>{generation++;if(timer)clearTimeout(timer);live.value=null;statusError.value='';if(props.modelValue)selectTask(displayTask(props.study)||{status:'NEW'});});
onBeforeUnmount(()=>{generation++;if(timer)clearTimeout(timer);});
</script>
