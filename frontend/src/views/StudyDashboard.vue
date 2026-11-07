<template>
  <ClinicalShell>
    <main class="c-main">
      <div class="c-pagehead"><div><h1>检查数据</h1><p>查看已导入的影像，跟进分析与阅片。</p></div><div class="c-row"><router-link v-if="auth.isAdmin" class="c-button c-ghost" to="/admin/users"><el-icon><User /></el-icon>账号管理</router-link><router-link class="c-button c-primary" to="/tasks/feature-export"><el-icon><DataAnalysis /></el-icon>特征计算</router-link><button class="c-button" @click="showImport=true"><el-icon><Plus /></el-icon>导入数据</button></div></div>
      <div class="c-stats">
        <button v-for="stat in stats" :key="stat.key" class="c-stat" :aria-pressed="filter===stat.key" @click="setFilter(stat.key)"><span class="c-stat-top">{{ stat.title }}<el-icon><component :is="stat.icon" /></el-icon></span><strong class="c-stat-value">{{ loaded?stat.count:'—' }}</strong><span class="c-small c-muted">{{ stat.hint }}</span></button>
      </div>
      <div v-if="fetchError" class="c-alert" role="alert" style="margin-bottom:18px">{{ fetchError }} <button class="c-link" @click="fetchStudies()">重试</button></div>
      <section class="c-panel" aria-label="检查列表" :aria-busy="loading">
        <div class="c-list-tools"><label class="c-search"><el-icon><Search /></el-icon><input v-model="search" placeholder="搜索检查名称" aria-label="搜索检查名称" @input="page=1"><button type="button" class="c-search-clear" :style="{visibility:search?'visible':'hidden'}" aria-label="清空搜索" @click="search='';page=1">×</button></label><div class="c-row"><select v-model="sortOrder" class="c-input c-sort" aria-label="检查排序" @change="page=1"><option value="import_desc">导入时间：从新到旧</option><option value="import_asc">导入时间：从旧到新</option><option disabled value="last_operation">最近操作时间（暂无记录）</option></select><button class="c-button c-ghost c-icon" :disabled="loading" aria-label="刷新检查列表" :title="'每 5 秒自动更新'+(refreshedAt?' · 最近更新 '+refreshedAt:'')" @click="fetchStudies()"><el-icon><Refresh /></el-icon></button></div></div>
        <div class="c-table-wrap"><table class="c-table c-study-table" :class="{'has-study-dates':hasStudyDates}"><colgroup><col style="width:5%"><col :style="{width:hasStudyDates?'13%':'15%'}"><col :style="{width:hasStudyDates?'17%':'20%'}"><col :style="{width:hasStudyDates?'14%':'16%'}"><col v-if="hasStudyDates" style="width:10%"><col :style="{width:hasStudyDates?'14%':'15%'}"><col :style="{width:hasStudyDates?'14%':'15%'}"><col :style="{width:hasStudyDates?'13%':'14%'}"></colgroup><thead><tr><th>编号</th><th>患者ID</th><th>检查名称</th><th>影像信息</th><th v-if="hasStudyDates">检查日期</th><th>导入时间</th><th>分析状态</th><th>操作</th></tr></thead><tbody>
          <tr v-for="item in visible" :key="item.id"><td class="c-numeric">{{ item.id }}</td><td class="c-code">{{ item.patient?.patient_id?.trim() || '—' }}</td><td><button class="c-link" style="white-space:normal;text-align:center;overflow-wrap:anywhere" @click="openWorkspace(item)">{{ item.study_description?.trim() || '未记录名称' }}</button></td><td>{{ item.modality || '—' }}<div class="c-subline">{{ numberText(item.slice_count,0,true) }} 层<span v-if="thickness(item)"> · 层厚 {{ thickness(item) }} mm</span></div></td><td v-if="hasStudyDates" class="c-numeric">{{ studyDate(item.study_date) }}</td><td class="c-numeric">{{ dateTime(item.created_at) }}</td><td><span class="c-badge" :class="statusTone(displayTask(item))">{{ statusLabel(displayTask(item)) }}</span><div v-if="isActive(displayTask(item))" class="c-stage-track" :aria-label="statusLabel(displayTask(item))"><span v-for="n in 4" :key="n" :class="{done:n<stage(item),current:n===stage(item)}"></span></div><div v-else class="c-subline">{{ latestTask(item)?.status==='COMPLETED'?'可查看病灶结果':latestTask(item)?.status==='FAILED'?'查看原因后可重试':'尚未提交分析' }}</div></td><td><div class="c-table-actions"><button class="c-button c-row-action" @click="primaryAction(item)">{{ actionLabel(item) }}</button><button v-if="!displayTask(item) || displayTask(item)?.status==='COMPLETED'" class="c-link c-muted" @click="openDetails(item)">{{ displayTask(item)?'记录':'详情' }}</button></div></td></tr>
          <tr v-if="!visible.length"><td :colspan="hasStudyDates?8:7"><div class="c-empty">{{ loading&&!loaded?'正在加载检查…':!loaded?'检查列表暂不可用':studies.length?'没有符合条件的检查。':'尚未导入检查，点击右上角“导入数据”开始。' }}</div></td></tr>
        </tbody></table></div>
        <div class="c-table-foot"><span aria-live="polite">{{ loaded?'共 '+filtered.length+' 份检查':'正在读取检查列表' }}</span><div class="c-row"><span>每页 8 条</span><button class="c-button c-icon" :disabled="page<=1" aria-label="上一页" @click="page--">‹</button><span>{{ page }} / {{ pages }}</span><button class="c-button c-icon" :disabled="page>=pages" aria-label="下一页" @click="page++">›</button></div></div>
      </section>
    </main>
    <AnalysisDetails v-model="showDetails" :study="selectedStudy" @open="openSelectedWorkspace" @submit="submitFromDetails" />
    <ClinicalDialog v-model="showImport" title="导入数据" :busy="uploading">
      <div class="c-upload"><el-icon :size="32" style="color:var(--c-blue)"><UploadFilled /></el-icon><h3 style="margin-top:10px;overflow-wrap:anywhere">选择 DICOM 影像文件</h3><p class="c-small c-muted" style="margin-top:5px">支持 ZIP 序列压缩包或单个 DICOM 文件。</p><input ref="fileInput" type="file" accept=".zip,.dcm" hidden @change="chooseFile"><button class="c-button" style="margin-top:17px" :disabled="uploading" @click="fileInput?.click()">{{ uploadFile?'重新选择文件':'选择文件' }}</button><p v-if="uploadFile" class="c-small c-muted" style="margin-top:10px;overflow-wrap:anywhere">{{ uploadFile.name }} · {{ (uploadFile.size/1024/1024).toFixed(1) }} MB</p></div>
      <template v-if="uploading"><p class="c-small c-muted">{{ uploadPercent>=100?'上传完成，正在解析检查…':'正在上传文件…' }}</p><div v-if="uploadPercent>0" class="c-progress" role="progressbar" :aria-valuenow="uploadPercent" aria-valuemin="0" aria-valuemax="100" aria-label="文件上传进度"><span :style="{width:uploadPercent+'%'}"></span></div></template>
      <p v-if="uploadError" class="c-alert" role="alert">{{ uploadError }}</p>
      <template #footer><button class="c-button" :disabled="uploading" @click="showImport=false">取消</button><button class="c-button c-primary" :disabled="!uploadFile||uploading" @click="importFile">{{ uploading?'正在导入…':'开始导入' }}</button></template>
    </ClinicalDialog>
    <ClinicalDialog v-model="showSubmit" :busy="submitting" title="提交分析" :subtitle="submitStudy?studyCode(submitStudy.id):''">
      <p class="c-muted">将此检查提交至病灶识别与定量分析流程。</p><dl v-if="submitStudy" style="margin-top:15px"><div class="c-detail-row"><dt>检查信息</dt><dd>{{ submitStudy.modality }} · {{ numberText(submitStudy.slice_count,0,true) }} 层</dd></div><div class="c-detail-row"><dt>处理状态</dt><dd>{{ statusLabel(displayTask(submitStudy)) }}</dd></div></dl><p class="c-small c-muted" style="margin-top:16px">提交后可留在检查列表查看状态。</p>
      <template #footer><button class="c-button" :disabled="submitting" @click="showSubmit=false">取消</button><button class="c-button c-primary" :disabled="submitting" @click="submitAnalysis">{{ submitting?'正在提交…':'确认提交' }}</button></template>
    </ClinicalDialog>
  </ClinicalShell>
</template>
<script setup lang="ts">
import { ref,computed,onMounted,onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import { Files,FullScreen,CircleCheck,Warning,Plus,Search,Refresh,DataAnalysis,UploadFilled,User } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import ClinicalShell from '@/components/ClinicalShell.vue';
import ClinicalDialog from '@/components/ClinicalDialog.vue';
import AnalysisDetails from '@/components/AnalysisDetails.vue';
import { useAuthStore } from '@/stores/auth';
import api from '@/api/client';
import { displayTask,latestTask,runningTask,isActive,taskId,statusLabel,statusTone,studyCode,studyDate,serverDate,dateTime,numberText,publicError,type Study } from '@/ui/clinical';
const auth=useAuthStore(),router=useRouter();
const studies=ref<Study[]>([]),loading=ref(false),loaded=ref(false),fetchError=ref(''),refreshedAt=ref('');
type Filter='all'|'new'|'active'|'completed'|'failed';
const filter=ref<Filter>('all'),search=ref(''),page=ref(1),sortOrder=ref('import_desc');
function matches(item:Study,key:Filter){const task=displayTask(item);return key==='all'||(key==='new'?!task:key==='active'?isActive(task):key==='completed'?task?.status==='COMPLETED':task?.status==='FAILED');}
const count=(key:Filter)=>studies.value.filter(s=>matches(s,key)).length;
const stats=computed(()=>[{key:'all' as Filter,title:'已导入检查',count:count('all'),hint:'当前账号可访问的检查',icon:Files},{key:'active' as Filter,title:'处理中',count:count('active'),hint:studies.value.filter(s=>{const t=runningTask(s);return t&&t.status!=='QUEUED';}).length+' 份执行中 · '+studies.value.filter(s=>runningTask(s)?.status==='QUEUED').length+' 份排队',icon:FullScreen},{key:'completed' as Filter,title:'分析完成',count:count('completed'),hint:'可进入工作台查看结果',icon:CircleCheck},{key:'failed' as Filter,title:'处理异常',count:count('failed'),hint:'查看原因后可重新提交',icon:Warning}]);
const filtered=computed(()=>studies.value.filter(s=>matches(s,filter.value)&&(s.study_description||'').toLowerCase().includes(search.value.trim().toLowerCase())).sort((a,b)=>{const delta=(Date.parse(serverDate(a.created_at))||0)-(Date.parse(serverDate(b.created_at))||0)||a.id-b.id;return sortOrder.value==='import_asc'?delta:-delta;}));
const hasStudyDates=computed(()=>studies.value.some(item=>studyDate(item.study_date)!=='—'));
const pages=computed(()=>Math.max(1,Math.ceil(filtered.value.length/8)));
const visible=computed(()=>filtered.value.slice((page.value-1)*8,page.value*8));
function setFilter(value:Filter){filter.value=value;page.value=1;}
function thickness(item:Study){const n=item.metadata_json?.slice_thickness_mm;return typeof n==='number'&&n>0?numberText(n,2):null;}
function stage(item:Study){return displayTask(item)?.status==='QUEUED'?1:displayTask(item)?.status==='SEGMENTING'?2:3;}
function actionLabel(item:Study){const t=displayTask(item);return !t?'提交分析':isActive(t)?'查看进度':t.status==='COMPLETED'?'查看结果':'查看原因';}
function openWorkspace(item:Study,task:number|null=null){router.push({path:'/workspace/'+item.id,query:task?{task:String(task)}:{}});}
const selectedId=ref<number|null>(null),showDetails=ref(false);
const selectedStudy=computed(()=>studies.value.find(s=>s.id===selectedId.value)||null);
function openDetails(item:Study){selectedId.value=item.id;showDetails.value=true;}
function openSelectedWorkspace(task:number|null){if(selectedStudy.value)openWorkspace(selectedStudy.value,task);showDetails.value=false;}
function submitFromDetails(){const s=selectedStudy.value;showDetails.value=false;if(s)openSubmit(s);}
function primaryAction(item:Study){const t=displayTask(item);if(!t)openSubmit(item);else if(t.status==='COMPLETED')openWorkspace(item,taskId(t));else openDetails(item);}
let timer:ReturnType<typeof setTimeout>|null=null,disposed=false;
async function fetchStudies(silent=false){if(loading.value)return;loading.value=true;try{const result:any=await api.get('/studies');if(disposed)return;studies.value=Array.isArray(result)?result:[];loaded.value=true;fetchError.value='';page.value=Math.min(page.value,pages.value);refreshedAt.value=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});}catch{if(!disposed)fetchError.value='检查列表读取失败，可稍后重试。';}finally{loading.value=false;}}
async function poll(){if(disposed)return;if(!document.hidden)await fetchStudies(true);if(!disposed)timer=setTimeout(poll,5000);}
const showImport=ref(false),fileInput=ref<HTMLInputElement|null>(null),uploadFile=ref<File|null>(null),uploading=ref(false),uploadPercent=ref(0),uploadError=ref('');
function chooseFile(event:Event){uploadFile.value=(event.target as HTMLInputElement).files?.[0]||null;uploadError.value='';uploadPercent.value=0;}
async function importFile(){if(!uploadFile.value||uploading.value)return;uploading.value=true;uploadError.value='';uploadPercent.value=0;const data=new FormData();data.append('file',uploadFile.value);try{await api.post('/studies/upload',data,{onUploadProgress:event=>{if(event.total)uploadPercent.value=Math.min(100,Math.round(event.loaded/event.total*100));}});showImport.value=false;uploadFile.value=null;if(fileInput.value)fileInput.value.value='';await fetchStudies();ElMessage.success('检查导入完成，可在列表中提交分析。');}catch(error){uploadError.value=publicError(error,'导入未完成，请检查文件格式后重试。');}finally{uploading.value=false;}}
const showSubmit=ref(false),submitStudy=ref<Study|null>(null),submitting=ref(false);
function openSubmit(item:Study){if(runningTask(item)){openDetails(item);return;}submitStudy.value=item;showSubmit.value=true;}
async function submitAnalysis(){if(!submitStudy.value||submitting.value)return;submitting.value=true;try{const latest:any=await api.get('/studies/'+submitStudy.value.id);if(runningTask(latest)){showSubmit.value=false;await fetchStudies();ElMessage.info('该检查已有任务正在处理。');return;}await api.post('/tasks/analyze',{study_id:submitStudy.value.id});showSubmit.value=false;await fetchStudies();ElMessage.success('分析任务已提交。');}catch{}finally{submitting.value=false;}}
onMounted(()=>{void fetchStudies().then(()=>{if(!disposed)timer=setTimeout(poll,5000);});});
onBeforeUnmount(()=>{disposed=true;if(timer)clearTimeout(timer);});
</script>
