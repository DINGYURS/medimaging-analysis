<template>
  <ClinicalShell>
    <main class="c-work"><nav class="c-work-nav" aria-label="检查操作"><div class="c-row"><router-link class="c-button c-ghost" to="/dashboard"><el-icon><ArrowLeft /></el-icon>检查列表</router-link><button class="c-button" :disabled="!study" @click="showDetails=true"><el-icon><Clock /></el-icon>分析记录</button><button class="c-button c-primary" :disabled="!resultTaskId||!lesions.length" @click="openFeatureExport"><el-icon><DataAnalysis /></el-icon>特征计算</button><button class="c-button" :aria-expanded="!collapsed" aria-controls="work-results" @click="collapsed=!collapsed"><el-icon><Fold /></el-icon>{{ collapsed?'展开结果':'收起结果' }}</button></div></nav>
      <p v-if="loadError" class="c-alert" role="alert" style="margin-bottom:16px">{{ loadError }} <button class="c-link" @click="loadStudy()">重新读取</button></p>
      <div class="c-work-grid" :class="{collapsed:collapsed}">
        <section class="c-view" :aria-busy="loading">
          <div class="c-view-toolbar">
            <div class="c-studyline"><h1 :title="study?'检查 ID '+studyCode(study.id):undefined">{{ study?studyHeading(study):'正在读取检查…' }}</h1><span class="c-badge neutral">{{ study?.modality || '—' }} · {{ maxSlices?maxSlices+' 层':'层数待读取' }}</span><span v-if="taskStatus" class="c-badge" :class="statusTone(taskStatus)">{{ statusLabel(taskStatus) }}</span></div>
            <div class="c-windowtools"><div class="c-presetsvalues"><div v-if="study?.modality==='CT'" class="c-segment" aria-label="窗宽窗位预设"><button :aria-pressed="preset==='肺窗'" @click="setWindow(1500,-600,'肺窗')">肺窗</button><button :aria-pressed="preset==='纵隔窗'" @click="setWindow(350,40,'纵隔窗')">纵隔窗</button><button :aria-pressed="preset==='骨窗'" @click="setWindow(2000,500,'骨窗')">骨窗</button><button :aria-pressed="preset==='脑窗'" @click="setWindow(80,40,'脑窗')">脑窗</button></div><span class="c-small c-muted c-numeric">WW {{ windowWidth }} / WL {{ windowCenter }}</span></div></div>
            <div class="c-toolbar-secondary"><span class="c-small c-muted">影像缩放</span><div class="c-row"><button class="c-button c-icon" aria-label="缩小影像" :disabled="zoom<=.6" @click="zoom=Math.max(.6,zoom-.2)">−</button><span class="c-small c-muted c-numeric">{{ Math.round(zoom*100) }}%</span><button class="c-button c-icon" aria-label="放大影像" :disabled="zoom>=2" @click="zoom=Math.min(2,zoom+.2)">+</button><button class="c-button c-ghost" @click="zoom=1">重置缩放</button></div></div>
          </div>
          <div ref="viewport" class="c-viewport" tabindex="0" aria-label="影像阅览区，使用上下方向键切换层面" @keydown="viewportKey" style="height:clamp(300px,64dvh,740px);aspect-ratio:auto;max-height:none" @wheel.prevent="wheel">
            <div class="c-image-stack" :style="stackStyle"><canvas ref="ctCanvas" :width="imageWidth" :height="imageHeight" aria-label="原始影像切片"></canvas><canvas ref="maskCanvas" :width="imageWidth" :height="imageHeight" aria-label="病灶掩膜叠加" style="pointer-events:none" :style="{opacity:showMask?maskOpacity/100:0}"></canvas><span v-if="selectedLocation && selectedLocation.slice===currentSlice && !imageLoading && !imageError" class="c-crosshair" :style="{left:selectedLocation.x+'%',top:selectedLocation.y+'%'}"><span class="c-crosshair-label">#{{ selectedLesion?.lesion_index }}</span></span></div>
            <div v-if="imageLoading||imageError||maskError" class="c-hud c-hud-bottom" role="status">{{ imageLoading?'正在读取切片…':imageError?imageError:'原始影像已显示；掩膜暂不可用' }}</div>
          </div>
          <div class="c-view-controls">
            <div class="c-between c-small c-muted"><span>当前层面 {{ currentSlice }} / {{ maxSlices || '—' }}</span><span v-if="selectedLocation">病灶 #{{ selectedLesion?.lesion_index }} · 中心层 {{ selectedLocation.slice }}</span><span v-else>全序列阅览</span></div>
            <div class="c-sliderline"><button class="c-button c-icon c-slice-prev" aria-label="上一层" :disabled="!maxSlices||currentSlice<=1" @click="changeSlice(currentSlice-1)">‹</button><div class="c-sliderwrap"><input type="range" min="1" :max="Math.max(1,maxSlices)" :value="currentSlice" :disabled="!maxSlices" aria-label="选择切片层数" @input="changeSlice(Number(($event.target as HTMLInputElement).value))"><div class="c-markers"><button v-for="point in markerLocations" :key="point.id" :style="{left:(point.slice-1)/Math.max(1,maxSlices-1)*100+'%'}" :aria-pressed="point.id===selectedId" :title="'病灶 #'+point.index+' · 中心层 '+point.slice" :aria-label="'定位病灶 '+point.index+'，第 '+point.slice+' 层'" @click="selectLesion(point.id)">{{ '#'+point.index+' · '+point.slice+'层' }}</button></div></div><button class="c-button c-icon c-slice-next" aria-label="下一层" :disabled="!maxSlices||currentSlice>=maxSlices" @click="changeSlice(currentSlice+1)">›</button><input class="c-input c-slice-input" type="number" min="1" :max="Math.max(1,maxSlices)" :value="currentSlice" :disabled="!maxSlices" aria-label="输入切片层数" @change="changeSlice(Number(($event.target as HTMLInputElement).value))"><span class="c-small c-muted c-slice-total">/ {{ maxSlices || '—' }}</span></div>
            <div class="c-toolsbottom"><div class="c-overlay-controls"><label><input v-model="showMask" type="checkbox" :disabled="!resultTaskId||!lesions.length">病灶掩膜</label><label>透明度<input v-model.number="maskOpacity" type="range" min="10" max="90" :disabled="!showMask" aria-label="掩膜透明度"><span>{{ maskOpacity }}%</span></label></div><p class="c-small c-muted c-selection-note" aria-live="polite">{{ selectionNote }}</p><div class="c-row" style="flex-wrap:nowrap;gap:8px"><button class="c-button" :disabled="selectedPosition<=0" @click="selectAdjacent(-1)">上一病灶</button><button class="c-button" :disabled="selectedPosition<0||selectedPosition>=orderedLesions.length-1" @click="selectAdjacent(1)">下一病灶</button></div></div>
          </div>
        </section>
        <aside id="work-results" v-show="!collapsed" class="c-result-panel" aria-label="分析结果">
          <template v-if="taskStatus?.status==='COMPLETED'">
            <div class="c-tabs" role="tablist" aria-label="结果类型"><button v-for="tab in tabs" :id="'work-tab-'+tab.id" :key="tab.id" role="tab" :aria-selected="activeTab===tab.id" :aria-controls="'work-panel-'+tab.id" @click="activeTab=tab.id">{{ tab.label }}</button></div>
            <section v-show="activeTab==='lesions'" id="work-panel-lesions" role="tabpanel" aria-labelledby="work-tab-lesions">
              <div class="c-between"><div><h2>候选病灶</h2><p class="c-small c-muted" style="margin-top:4px">点击卡片定位对应中心层</p></div><select v-if="lesions.length>1" v-model="sort" class="c-input c-small" aria-label="病灶排序"><option value="volume">按体积（大到小）</option><option value="slice">按层面（小到大）</option></select></div>
              <p v-if="resultError" class="c-alert" role="alert" style="margin-top:14px">{{ resultError }} <button class="c-link" @click="loadResults()">重试</button></p>
              <div v-if="!resultsLoaded&&!resultError" class="c-empty">正在读取结果…</div>
              <div v-if="resultsLoaded&&!lesions.length" class="c-result-block" style="margin-top:14px"><h3>未检出候选病灶</h3><p style="margin-top:8px">本次结果未返回候选病灶，仍可继续阅览完整影像。</p></div>
              <button v-for="lesion in orderedLesions" :key="lesion.id" class="c-lesion-card" :aria-pressed="selectedId===lesion.id" @click="selectLesion(lesion.id)">
                <span class="c-lesion-top"><span>病灶 #{{ lesion.lesion_index }}</span><span class="c-badge" :class="selectedId===lesion.id?'active':'neutral'">{{ selectedId===lesion.id?'当前选中':locationOf(lesion)?'点击定位':'定位信息不足' }}</span></span>
                <span class="c-lesion-metrics"><span><small>最大三维直径</small><strong>{{ numberText(metric(lesion,'diameter')) }}</strong><em>mm</em></span><span><small>病灶体积</small><strong>{{ numberText(metric(lesion,'volume'),1,true) }}</strong><em>mm³</em></span></span>
                <span class="c-small c-muted">{{ locationOf(lesion)?'中心层 '+locationOf(lesion)!.slice:'暂无可靠层面定位' }}</span>
                <span v-if="selectedId===lesion.id" class="c-lesion-extra" style="display:block">等效球径 {{ numberText(metric(lesion,'equivalent')) }} mm<span v-if="metric(lesion,'mean')!==null"> · 平均强度 {{ numberText(metric(lesion,'mean')) }}{{ intensityUnit }}</span></span>
              </button>
            </section>
            <section v-show="activeTab==='features'" id="work-panel-features" role="tabpanel" aria-labelledby="work-tab-features">
              <div class="c-result-block"><div class="c-between"><h2>{{ selectedLesion?'病灶 #'+selectedLesion.lesion_index:'暂无病灶指标' }}</h2><span class="c-badge neutral">定量指标</span></div><dl style="margin-top:12px"><div v-for="row in detailMetrics" :key="row.label" class="c-detail-row"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl><p class="c-small" style="margin-top:14px">最大三维直径与等效球径分别命名。缺少的指标显示“—”。</p></div>
              <div class="c-result-block"><h3>计算信息</h3><dl style="margin-top:10px"><div class="c-detail-row"><dt>特征数量</dt><dd>{{ numberText(selectedLesion?.features?.total_extracted_features_count,0,true) }}</dd></div><div class="c-detail-row"><dt>计算时间</dt><dd>{{ calculationTime(selectedLesion?.features?.provenance?.extracted_at) }}</dd></div><div class="c-detail-row"><dt>强度单位</dt><dd>{{ intensityUnit || '未确认' }}</dd></div></dl><button class="c-link" style="margin-top:15px" :disabled="!selectedLesion?.features" @click="downloadFeatures">导出当前病灶 JSON</button></div>
            </section>
            <section v-show="activeTab==='insights'" id="work-panel-insights" role="tabpanel" aria-labelledby="work-tab-insights">
              <div class="c-result-block"><div class="c-between"><h2>AI 洞察</h2><span v-if="!hasInsights" class="c-badge neutral">大模型待接入</span></div><p style="margin:17px 0">结合当前检查的病灶摘要与定量指标，呈现结构化分析内容。</p><div v-for="item in insightSections" :key="item.title" class="c-insight-section"><h3>{{ item.title }}</h3><p style="white-space:pre-wrap">{{ item.value || item.placeholder }}</p></div><button v-if="!hasInsights" class="c-button c-primary" disabled style="margin-top:13px"><el-icon><MagicStick /></el-icon>生成 AI 洞察</button><p v-if="!hasInsights" class="c-small" style="margin-top:12px">大模型尚未接入，当前不生成分析内容。</p></div>
              <details v-if="report?.id" class="c-result-block c-disclosure"><summary>医生复核记录{{ report.audit_status==='DOCTOR_CONFIRMED'?' · 已确认':'' }}</summary><label class="c-field">影像所见<textarea v-model="doctorFindings" class="c-input" rows="4" placeholder="填写医生复核后的影像所见"></textarea></label><label class="c-field">诊断意见<textarea v-model="doctorImpression" class="c-input" rows="3" placeholder="填写医生复核意见"></textarea></label><button class="c-button c-primary" style="margin-top:16px" :disabled="savingReport" @click="saveDoctorReport">{{ savingReport?'正在保存…':'签署确认并归档' }}</button></details>
            </section>
          </template>
          <div v-else class="c-result-block">
            <div class="c-between"><h2>{{ taskStatus?.status==='FAILED'?'本次分析未完成':taskStatus?'分析进度':'尚未分析' }}</h2><span class="c-badge" :class="statusTone(taskStatus)">{{ statusLabel(taskStatus) }}</span></div>
            <p style="margin-top:12px">{{ taskStatus?.status==='FAILED'?'可在分析记录中查看任务状态，原始影像仍可阅览。':taskStatus?'可继续阅览原始影像，结果生成后在这里查看。':'导入已完成，可提交病灶识别与定量分析。' }}</p>
            <template v-if="isActive(taskStatus)"><div v-for="(step,index) in steps" :key="step" class="c-step" :class="{done:index<stage,current:index===stage}"><span class="c-step-number">{{ index<stage?'✓':index+1 }}</span><div><h3>{{ step }}</h3></div></div></template>
            <button v-if="!taskStatus||taskStatus.status==='FAILED'" class="c-button c-primary" style="margin-top:16px" @click="taskStatus?showDetails=true:showSubmit=true">{{ taskStatus?'查看原因并重试':'提交分析' }}</button>
            <p v-if="pollError" class="c-small" role="status" style="margin-top:12px">{{ pollError }}</p>
          </div>
        </aside>
      </div>
    </main>
    <AnalysisDetails v-model="showDetails" :study="study" @open="openTask" @submit="showDetails=false;showSubmit=true" />
    <ClinicalDialog v-model="showSubmit" :busy="submitting" title="提交分析" :subtitle="study?studyCode(study.id):''"><p class="c-muted">提交当前检查进行病灶识别与定量分析。已有分析任务可在“分析记录”中查看。</p><template #footer><button class="c-button" :disabled="submitting" @click="showSubmit=false">取消</button><button class="c-button c-primary" :disabled="submitting" @click="submitAnalysis">{{ submitting?'正在提交…':'确认提交' }}</button></template></ClinicalDialog>
  </ClinicalShell>
</template>
<script setup lang="ts">
import { ref,computed,watch,onMounted,onBeforeUnmount,nextTick,type CSSProperties } from 'vue';
import { useRoute,useRouter } from 'vue-router';
import { ArrowLeft,Clock,DataAnalysis,Fold,MagicStick } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import ClinicalShell from '@/components/ClinicalShell.vue';
import ClinicalDialog from '@/components/ClinicalDialog.vue';
import AnalysisDetails from '@/components/AnalysisDetails.vue';
import { readWorkspaceView } from '@/ui/workspaceNavigation';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import { sortedTasks,displayTask,runningTask,taskId,isActive,studyCode,statusLabel,statusTone,numberText,metric,locateLesion,studyHeading,calculationTime,type Study,type Lesion,type AnalysisTask } from '@/ui/clinical';
const route=useRoute(),router=useRouter(),auth=useAuthStore();
const studyId=computed(()=>Number(route.params.studyId)),study=ref<Study|null>(null),taskStatus=ref<AnalysisTask|null>(null),lesions=ref<Lesion[]>([]),report=ref<any>(null),loading=ref(true),loadError=ref(''),resultsLoaded=ref(false),resultError=ref(''),pollError=ref('');
const resultTaskId=ref<number|null>(null),selectedId=ref<number|null>(null),activeTab=ref('lesions'),sort=ref('volume'),collapsed=ref(false);
const maxSlices=computed(()=>Number.isInteger(study.value?.slice_count)&&study.value!.slice_count>0?study.value!.slice_count:0);
const currentSlice=ref(1),windowWidth=ref(1500),windowCenter=ref(-600),preset=ref('肺窗'),showMask=ref(true),maskOpacity=ref(50),zoom=ref(1);
const imageWidth=ref(512),imageHeight=ref(512),viewport=ref<HTMLElement|null>(null),ctCanvas=ref<HTMLCanvasElement|null>(null),maskCanvas=ref<HTMLCanvasElement|null>(null),fitWidth=ref(512),fitHeight=ref(512);
const imageLoading=ref(false),imageError=ref(''),maskError=ref(false);
const selectedLesion=computed(()=>lesions.value.find(l=>l.id===selectedId.value)||null);
const selectedLocation=computed(()=>selectedLesion.value?locateLesion(study.value,selectedLesion.value):null);
const intensityUnit=computed(()=>study.value?.metadata_json?.intensity_unit==='HU'||study.value?.metadata_json?.rescale_type==='HU'?' HU':'');
const orderedLesions=computed(()=>[...lesions.value].sort((a,b)=>sort.value==='slice'?(locationOf(a)?.slice??Infinity)-(locationOf(b)?.slice??Infinity)||a.lesion_index-b.lesion_index:(b.volume_mm3??-Infinity)-(a.volume_mm3??-Infinity)||a.lesion_index-b.lesion_index));
const selectedPosition=computed(()=>orderedLesions.value.findIndex(l=>l.id===selectedId.value));
const markerLocations=computed(()=>lesions.value.flatMap(l=>{const p=locationOf(l);return p?[{id:l.id,index:l.lesion_index,slice:p.slice}]:[];}));
const stackStyle=computed<CSSProperties>(()=>({width:fitWidth.value+'px',height:fitHeight.value+'px',aspectRatio:'auto',transform:'scale('+zoom.value+')'}));
const selectionNote=computed(()=>!selectedLesion.value?taskStatus.value?.status==='COMPLETED'&&resultsLoaded.value?'可继续阅览全序列。':'原始影像可继续阅览。':!selectedLocation.value?'当前病灶缺少可靠定位信息。':currentSlice.value===selectedLocation.value.slice?'已定位病灶 #'+selectedLesion.value.lesion_index+' 的中心层。':'当前为邻近层，点击病灶卡片可重新定位。');
const tabs=[{id:'lesions',label:'病灶摘要'},{id:'features',label:'详细指标'},{id:'insights',label:'AI 洞察'}];
const steps=['等待处理','病灶分析','结果整理','分析完成'];
const stage=computed(()=>taskStatus.value?.status==='QUEUED'?0:taskStatus.value?.status==='SEGMENTING'?1:2);
const detailMetrics=computed(()=>[{label:'最大三维直径',value:numberText(metric(selectedLesion.value,'diameter'))+' mm'},{label:'病灶体积',value:numberText(metric(selectedLesion.value,'volume'),1,true)+' mm³'},{label:'等效球径',value:numberText(metric(selectedLesion.value,'equivalent'))+' mm'},{label:'平均强度',value:numberText(metric(selectedLesion.value,'mean'))+intensityUnit.value},{label:'球形度',value:numberText(metric(selectedLesion.value,'sphericity'),4)},{label:'定位中心层',value:selectedLocation.value?.slice||'—'}]);
const hasInsights=computed(()=>report.value?.status!=='PENDING_INTEGRATION'&&!!(report.value?.findings||report.value?.impression||report.value?.recommendations));
const insightSections=computed(()=>[{title:'影像所见',value:hasInsights.value?report.value?.findings:'',placeholder:'接入大模型后展示影像所见摘要。'},{title:'定量解读',value:hasInsights.value?report.value?.impression:'',placeholder:'结合病灶测量与组学结果整理关键发现。'},{title:'关注要点',value:hasInsights.value?report.value?.recommendations:'',placeholder:'展示需要医生复核的内容及分析依据。'}]);
const showDetails=ref(false),showSubmit=ref(false),submitting=ref(false),doctorFindings=ref(''),doctorImpression=ref(''),savingReport=ref(false);
let disposed=false,loadGeneration=0,renderGeneration=0,resultGeneration=0,pollTimer:ReturnType<typeof setTimeout>|null=null,observer:ResizeObserver|null=null,renderController:AbortController|null=null;
let warmController:AbortController|null=null;
const imageCache=new Map<string,ImageBitmap>();
function cachePut(key:string,image:ImageBitmap){imageCache.set(key,image);if(imageCache.size>40){const oldest=imageCache.keys().next().value;if(oldest){imageCache.get(oldest)?.close();imageCache.delete(oldest);}}}
function clearCache(){imageCache.forEach(image=>image.close());imageCache.clear();}
function resize(){const v=viewport.value;if(!v)return;const ratio=imageWidth.value/imageHeight.value;fitWidth.value=Math.min(v.clientWidth,v.clientHeight*ratio);fitHeight.value=fitWidth.value/ratio;}
function locationOf(lesion:Lesion){return locateLesion(study.value,lesion);}
function selectLesion(id:number){selectedId.value=id;const lesion=lesions.value.find(l=>l.id===id);const point=lesion?locationOf(lesion):null;if(point)changeSlice(point.slice);}
function selectAdjacent(delta:number){const item=orderedLesions.value[selectedPosition.value+delta];if(item)selectLesion(item.id);}
function changeSlice(value:number){if(!maxSlices.value)return;currentSlice.value=Math.max(1,Math.min(maxSlices.value,Math.round(value)||1));void renderCurrent();}
function viewportKey(event:KeyboardEvent){if(event.target!==event.currentTarget||!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();changeSlice(event.key==='Home'?1:event.key==='End'?maxSlices.value:currentSlice.value+(event.key==='ArrowDown'?1:-1));}
function wheel(event:WheelEvent){if(!maxSlices.value)return;changeSlice(currentSlice.value+(event.deltaY>0?1:-1));}
function setWindow(width:number,center:number,name:string){if(width===windowWidth.value&&center===windowCenter.value)return;windowWidth.value=width;windowCenter.value=center;preset.value=name;void renderCurrent();}
function imageUrl(slice:number,mask=false){const params=new URLSearchParams(mask?{task_id:String(resultTaskId.value)}:{ww:String(windowWidth.value),wc:String(windowCenter.value),overlay_mask:'false'});return '/api/v1/studies/'+studyId.value+'/'+(mask?'mask-slice/':'slice/')+slice+'?'+params;}
async function readImage(url:string,signal?:AbortSignal){const cached=imageCache.get(url);if(cached)return cached;const response=await fetch(url,{headers:auth.token?{Authorization:'Bearer '+auth.token}:{},signal});if(!response.ok)throw new Error('image-unavailable');const image=await createImageBitmap(await response.blob());if(signal?.aborted||disposed){image.close();throw new DOMException('Aborted','AbortError');}cachePut(url,image);return image;}
function warmNeighbors(center:number,version:number){
 if(disposed)return;warmController?.abort();warmController=new AbortController();const signal=warmController.signal;
 const neighbors=[center+1,center-1,center+2,center-2].filter(n=>n>=1&&n<=maxSlices.value);
 const urls=neighbors.flatMap(n=>resultTaskId.value&&lesions.value.length?[imageUrl(n),imageUrl(n,true)]:[imageUrl(n)]);
 let cursor=0;
 async function worker(){while(cursor<urls.length&&!disposed&&!signal.aborted&&version===loadGeneration){const url=urls[cursor++];try{await readImage(url,signal);}catch{if(signal.aborted)return;}}}
 void Promise.all([worker(),worker()]);
}
function clearCanvas(canvas:HTMLCanvasElement|null){if(canvas)canvas.getContext('2d')?.clearRect(0,0,canvas.width,canvas.height);}
async function renderCurrent(){
 if(!maxSlices.value||disposed)return;
 const version=++renderGeneration,slice=currentSlice.value,studyVersion=loadGeneration;
 warmController?.abort();renderController?.abort();renderController=new AbortController();const signal=renderController.signal;
 imageLoading.value=true;imageError.value='';maskError.value=false;clearCanvas(ctCanvas.value);clearCanvas(maskCanvas.value);
 try{
  const image=await readImage(imageUrl(slice),signal);if(version!==renderGeneration||studyVersion!==loadGeneration||disposed)return;
  imageWidth.value=image.width;imageHeight.value=image.height;await nextTick();if(version!==renderGeneration||disposed)return;
  resize();ctCanvas.value?.getContext('2d')?.drawImage(image,0,0,image.width,image.height);imageLoading.value=false;
  if(resultTaskId.value&&lesions.value.length){
   try{const mask=await readImage(imageUrl(slice,true),signal);if(version!==renderGeneration||studyVersion!==loadGeneration||disposed)return;if(mask.width!==image.width||mask.height!==image.height){maskError.value=true;return;}maskCanvas.value?.getContext('2d')?.drawImage(mask,0,0,image.width,image.height);}
   catch(error){if((error as Error).name!=='AbortError'&&version===renderGeneration)maskError.value=true;}
  }
  if(version===renderGeneration)warmNeighbors(slice,studyVersion);
 }catch(error){if((error as Error).name!=='AbortError'&&version===renderGeneration){imageError.value='该切片读取失败，请切换层面或重新读取检查。';imageLoading.value=false;}}
}
async function loadResults(){
 const id=taskId(taskStatus.value);if(!id||taskStatus.value?.status!=='COMPLETED')return;
 const version=++resultGeneration,studyVersion=loadGeneration;resultError.value='';resultsLoaded.value=false;
 try{const result:any=await api.get('/results/'+id);if(disposed||studyVersion!==loadGeneration||version!==resultGeneration)return;lesions.value=Array.isArray(result.lesions)?result.lesions:[];resultTaskId.value=id;report.value=result.report||null;doctorFindings.value=report.value?.doctor_findings||'';doctorImpression.value=report.value?.doctor_impression||'';resultsLoaded.value=true;selectedId.value=orderedLesions.value[0]?.id||null;const point=selectedLesion.value?locationOf(selectedLesion.value):null;if(point)currentSlice.value=point.slice;restoreWorkspaceView();await renderCurrent();}
 catch{if(!disposed&&version===resultGeneration)resultError.value='分析结果读取失败，请重试。';}
}
function schedulePoll(version:number){if(disposed||!isActive(taskStatus.value))return;pollTimer=setTimeout(async()=>{if(disposed||version!==loadGeneration)return;const id=taskId(taskStatus.value);if(!id)return;try{const result:any=await api.get('/tasks/'+id+'/status');if(disposed||version!==loadGeneration)return;taskStatus.value=result;pollError.value='';if(result.status==='COMPLETED'){await loadResults();return;}}catch{if(version===loadGeneration)pollError.value='状态更新暂不可用，稍后自动重试。';}if(version===loadGeneration)schedulePoll(version);},4000);}
async function resolveIntensityUnit(data:Study,version:number){
 if(data.metadata_json?.intensity_unit==='HU'||data.metadata_json?.rescale_type==='HU')return;
 try{const result:any=await api.get('/studies/'+data.id+'/intensity-unit');
  if(disposed||version!==loadGeneration||study.value?.id!==data.id)return;
  if(result.unit==='HU')study.value={...study.value,metadata_json:{...study.value.metadata_json,intensity_unit:'HU',intensity_unit_source:result.source}};
 }catch{/* Keep unknown units explicit when originals cannot be verified. */}
}
async function loadStudy(){
 const version=++loadGeneration;resultGeneration++;if(pollTimer)clearTimeout(pollTimer);warmController?.abort();renderController?.abort();renderGeneration++;clearCache();loading.value=true;loadError.value='';pollError.value='';resultsLoaded.value=false;resultError.value='';study.value=null;taskStatus.value=null;lesions.value=[];selectedId.value=null;resultTaskId.value=null;report.value=null;currentSlice.value=1;clearCanvas(ctCanvas.value);clearCanvas(maskCanvas.value);
 if(!Number.isInteger(studyId.value)||studyId.value<1){loadError.value='检查编号无效。';loading.value=false;return;}
 try{const data:any=await api.get('/studies/'+studyId.value);if(disposed||version!==loadGeneration)return;study.value=data;void resolveIntensityUnit(data,version);const requested=Number(route.query.task);const tasks=sortedTasks(data);taskStatus.value=tasks.find(t=>taskId(t)===requested)||displayTask(data);currentSlice.value=Math.max(1,Math.round(maxSlices.value/2));zoom.value=1;
  const m=data.metadata_json||{};if(data.modality!=='CT'&&typeof m.window_width==='number'&&m.window_width>0&&typeof m.window_center==='number'){windowWidth.value=m.window_width;windowCenter.value=m.window_center;preset.value='原始窗口';}
  else{windowWidth.value=1500;windowCenter.value=-600;preset.value='肺窗';}
  await nextTick();resize();if(taskStatus.value?.status==='COMPLETED'){void renderCurrent();await loadResults();}else{await renderCurrent();schedulePoll(version);}
 }catch{if(!disposed&&version===loadGeneration)loadError.value='检查读取失败或无权访问，请返回列表后重试。';}
 finally{if(version===loadGeneration)loading.value=false;}
}
function openTask(id:number|null){showDetails.value=false;if(id)router.replace({path:'/workspace/'+studyId.value,query:{task:String(id)}});}
async function submitAnalysis(){if(submitting.value||!study.value)return;submitting.value=true;try{const fresh:any=await api.get('/studies/'+studyId.value);if(runningTask(fresh)){showSubmit.value=false;await loadStudy();ElMessage.info('该检查已有任务正在处理。');return;}const response:any=await api.post('/tasks/analyze',{study_id:studyId.value});showSubmit.value=false;const id=taskId(response);if(id)await router.replace({path:'/workspace/'+studyId.value,query:{task:String(id)}});else await loadStudy();ElMessage.success('分析任务已提交。');}catch{}finally{submitting.value=false;}}
function restoreWorkspaceView(){
 const saved=readWorkspaceView(window.history.state?.workspaceView,studyId.value,resultTaskId.value,maxSlices.value,lesions.value.map(l=>l.id));
 if(!saved)return;
 currentSlice.value=saved.slice;windowWidth.value=saved.windowWidth;windowCenter.value=saved.windowCenter;preset.value=saved.preset;zoom.value=saved.zoom;
 selectedId.value=saved.selectedId;activeTab.value=saved.activeTab;sort.value=saved.sort;collapsed.value=saved.collapsed;showMask.value=saved.showMask;maskOpacity.value=saved.maskOpacity;
}
function openFeatureExport(){
 if(!resultTaskId.value||!lesions.value.length)return;
 const view={studyId:studyId.value,taskId:resultTaskId.value,slice:currentSlice.value,windowWidth:windowWidth.value,windowCenter:windowCenter.value,preset:preset.value,zoom:zoom.value,selectedId:selectedId.value,activeTab:activeTab.value,sort:sort.value,collapsed:collapsed.value,showMask:showMask.value,maskOpacity:maskOpacity.value};
 // Save this entry too, so the browser Back button restores the same viewer.
 window.history.replaceState({...window.history.state,workspaceView:view},'');
 void router.push({name:'FeatureExport',query:{study:String(studyId.value),task:String(resultTaskId.value),from:'workspace'},state:{workspaceView:view}});
}
async function saveDoctorReport(){if(!report.value?.id||savingReport.value)return;savingReport.value=true;try{const result:any=await api.put('/reports/'+report.value.id,{doctor_findings:doctorFindings.value,doctor_impression:doctorImpression.value,audit_status:'DOCTOR_CONFIRMED'});report.value={...report.value,...result};ElMessage.success('医生复核记录已保存。');}catch{}finally{savingReport.value=false;}}
function downloadFeatures(){if(!selectedLesion.value?.features)return;const blob=new Blob([JSON.stringify({lesion_id:selectedLesion.value.lesion_index,all_raw_features:selectedLesion.value.features.all_raw_features||{}},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=studyCode(studyId.value)+'_lesion_'+selectedLesion.value.lesion_index+'_features.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
watch(()=>[route.params.studyId,route.query.task],()=>{if(!disposed)void loadStudy();});
onMounted(()=>{observer=new ResizeObserver(resize);if(viewport.value)observer.observe(viewport.value);void loadStudy();});
onBeforeUnmount(()=>{disposed=true;loadGeneration++;renderGeneration++;resultGeneration++;if(pollTimer)clearTimeout(pollTimer);observer?.disconnect();warmController?.abort();renderController?.abort();clearCache();});
</script>
