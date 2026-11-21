<template>
  <ClinicalShell>
    <main class="c-work"><nav class="c-work-nav" aria-label="检查操作"><div class="c-row"><router-link class="c-link c-back-link" to="/dashboard"><el-icon><ArrowLeft /></el-icon>检查列表</router-link><button class="c-button" :disabled="!study" @click="showDetails=true"><el-icon><Clock /></el-icon>分析记录</button><button v-if="collapsed" class="c-button c-primary c-feature-action" :disabled="!resultTaskId||!lesions.length" @click="openFeatureExport"><el-icon><DataAnalysis /></el-icon>特征计算</button><button class="c-button" :aria-expanded="!collapsed" aria-controls="work-results" @click="toggleResults"><el-icon><Fold /></el-icon>{{ collapsed?'展开结果':'收起结果' }}</button></div></nav>
      <p v-if="loadError" class="c-alert" role="alert" style="margin-bottom:16px">{{ loadError }} <button class="c-link" @click="loadStudy()">重新读取</button></p>
      <div class="c-work-grid" :class="{collapsed:collapsed}">
        <section class="c-view" :aria-busy="loading">
          <div class="c-view-toolbar">
            <div class="c-studyline"><h1 :title="study?'检查 ID '+studyCode(study.id):undefined">{{ study?studyHeading(study):'正在读取检查…' }}</h1><span class="c-badge neutral">{{ study?.modality || '—' }} · {{ maxSlices?maxSlices+' 层':'层数待读取' }}</span><span v-if="taskStatus" class="c-badge" :class="statusTone(taskStatus)">{{ statusLabel(taskStatus) }}</span></div>
            <div v-if="relatedSeries.length > 1" class="c-row">
              <label for="mri-series-select" class="c-small c-muted">MRI 序列</label>
              <select id="mri-series-select" class="c-button" :value="studyId" @change="openSeries">
                <option v-for="series in relatedSeries" :key="series.id" :value="series.id">
                  {{ series.label }} · {{ series.slice_count }} 层
                </option>
              </select>
            </div>
            <div class="c-windowtools"><div class="c-presetsvalues"><div v-if="study?.modality==='CT'" class="c-segment" aria-label="窗宽窗位预设"><button :aria-pressed="preset==='肺窗'" @click="setWindow(1500,-600,'肺窗')">肺窗</button><button :aria-pressed="preset==='纵隔窗'" @click="setWindow(350,40,'纵隔窗')">纵隔窗</button><button :aria-pressed="preset==='骨窗'" @click="setWindow(2000,500,'骨窗')">骨窗</button><button :aria-pressed="preset==='脑窗'" @click="setWindow(80,40,'脑窗')">脑窗</button></div><span class="c-small c-muted c-numeric">WW {{ windowWidth }} / WL {{ windowCenter }}</span></div></div>
            <div class="c-toolbar-secondary"><span class="c-small c-muted">影像缩放</span><div class="c-row"><button class="c-button c-icon" aria-label="缩小影像" :disabled="zoom<=.6" @click="zoom=Math.max(.6,zoom-.2)">−</button><span class="c-small c-muted c-numeric">{{ Math.round(zoom*100) }}%</span><button class="c-button c-icon" aria-label="放大影像" :disabled="zoom>=2" @click="zoom=Math.min(2,zoom+.2)">+</button><button class="c-button c-ghost" @click="resetView">重置缩放</button></div></div>
          </div>
          <div ref="viewport" class="c-viewport" tabindex="0" aria-label="影像阅览区，上下方向键或滚轮切层，Ctrl 加滚轮缩放，Ctrl 加左键拖动" :class="{'is-panning':panActive}" @pointerdown="startPan" @pointermove="movePan" @pointerup="endPan" @pointercancel="endPan" @lostpointercapture="endPan" @keydown="viewportKey" style="height:clamp(300px,64dvh,740px);aspect-ratio:auto;max-height:none" @wheel.prevent="wheel">
            <div class="c-image-stack" :style="stackStyle"><canvas ref="ctCanvas" width="512" height="512" aria-label="原始影像切片"></canvas><canvas ref="maskCanvas" width="512" height="512" aria-label="病灶掩膜叠加" style="pointer-events:none" :style="{opacity:showMask?maskOpacity/100:0}"></canvas><svg v-if="activeReferenceLine" aria-hidden="true" :viewBox="'0 0 '+imageWidth+' '+imageHeight"
                style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">
                <line :x1="activeReferenceLine[0][0]+0.5" :y1="activeReferenceLine[0][1]+0.5"
                  :x2="activeReferenceLine[1][0]+0.5" :y2="activeReferenceLine[1][1]+0.5"
                  stroke="#ffc857" stroke-width="2" stroke-linecap="round" vector-effect="non-scaling-stroke" />
              </svg><svg v-if="activeInsightBox" :aria-label="insightAnchor?.trial ? '胸部模型候选区' : '毛刺候选区域'"
                viewBox="0 0 1 1" preserveAspectRatio="none"
                style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">
                <rect :x="activeInsightBox[0]" :y="activeInsightBox[1]" :width="activeInsightBox[2]-activeInsightBox[0]"
                  :height="activeInsightBox[3]-activeInsightBox[1]" stroke="#53e3c2" stroke-width="2"
                  fill="none" vector-effect="non-scaling-stroke" />
              </svg><span v-if="selectedLocation && selectedLocation.slice===displayedSlice && !activeReferenceLine && !activeInsightBox" class="c-crosshair" :style="{left:selectedLocation.x+'%',top:selectedLocation.y+'%'}"><span class="c-crosshair-label">#{{ selectedLesion?.lesion_index }}</span></span></div>
            <div v-if="imageLoading||imageError||maskError" class="c-hud c-hud-bottom" role="status">{{ imageLoading?(displayedSlice?'正在读取第 '+currentSlice+' 层，当前显示第 '+displayedSlice+' 层…':'正在读取切片…'):imageError?imageError:'原始影像已显示；掩膜暂不可用' }}</div>
          </div>
          <div class="c-view-controls">
            <div class="c-between c-small c-muted"><span>当前层面 {{ currentSlice }} / {{ maxSlices || '—' }}</span><span v-if="selectedLocation">病灶 #{{ selectedLesion?.lesion_index }} · 中心层 {{ selectedLocation.slice }}</span><span v-else>全序列阅览</span></div>
            <div class="c-sliderline"><button class="c-button c-icon c-slice-prev" aria-label="上一层" :disabled="!maxSlices||currentSlice<=1" @click="changeSlice(currentSlice-1)">‹</button><div class="c-sliderwrap"><input type="range" min="1" :max="Math.max(1,maxSlices)" :value="currentSlice" :disabled="!maxSlices" aria-label="选择切片层数" @input="changeSlice(Number(($event.target as HTMLInputElement).value))"><div class="c-markers"><button v-for="point in markerLocations" :key="point.id" :style="{left:(point.slice-1)/Math.max(1,maxSlices-1)*100+'%'}" :aria-pressed="point.id===selectedId" :title="'病灶 #'+point.index+' · 中心层 '+point.slice" :aria-label="'定位病灶 '+point.index+'，第 '+point.slice+' 层'" @click="selectLesion(point.id)">{{ '#'+point.index+' · '+point.slice+'层' }}</button></div></div><button class="c-button c-icon c-slice-next" aria-label="下一层" :disabled="!maxSlices||currentSlice>=maxSlices" @click="changeSlice(currentSlice+1)">›</button><input class="c-input c-slice-input" type="number" min="1" :max="Math.max(1,maxSlices)" :value="currentSlice" :disabled="!maxSlices" aria-label="输入切片层数" @change="changeSlice(Number(($event.target as HTMLInputElement).value))"><span class="c-small c-muted c-slice-total">/ {{ maxSlices || '—' }}</span></div>
            <div class="c-toolsbottom"><div class="c-overlay-controls"><label><input v-model="showMask" type="checkbox" :disabled="!resultTaskId||!lesions.length">病灶掩膜</label><label>透明度<input v-model.number="maskOpacity" type="range" min="10" max="90" :disabled="!showMask" aria-label="掩膜透明度"><span>{{ maskOpacity }}%</span></label></div><p class="c-small c-muted c-selection-note" aria-live="polite">{{ selectionNote }}</p><div class="c-row" style="flex-wrap:nowrap;gap:8px"><button class="c-button" :disabled="selectedPosition<=0" @click="selectAdjacent(-1)">上一病灶</button><button class="c-button" :disabled="selectedPosition<0||selectedPosition>=orderedLesions.length-1" @click="selectAdjacent(1)">下一病灶</button></div></div>
          </div>
        </section>
        <div class="c-results-region" :inert="collapsed" :aria-hidden="collapsed"><aside id="work-results" ref="resultPanel" class="c-result-panel" aria-label="分析结果">
          <template v-if="taskStatus?.status==='COMPLETED'">
            <div class="c-feature-entry">
              <button class="c-button c-feature-action" :disabled="!resultTaskId||!lesions.length" @click="openFeatureExport"><el-icon><DataAnalysis /></el-icon>特征计算</button>
            </div>
            <div class="c-tabs" role="tablist" aria-label="结果类型"><button v-for="tab in tabs.filter(item => study?.modality === 'CT' || item.id === 'lesions')" :id="'work-tab-'+tab.id" :key="tab.id" role="tab" :aria-selected="activeTab===tab.id" :aria-controls="'work-panel-'+tab.id" @click="activeTab=tab.id">{{ tab.label }}</button></div>
            <section v-show="activeTab==='lesions'" id="work-panel-lesions" role="tabpanel" aria-labelledby="work-tab-lesions">
              <div class="c-between"><div><h2>候选病灶</h2><p class="c-small c-muted" style="margin-top:4px">点击卡片定位对应中心层</p></div><ClinicalSelect v-if="lesions.length>1" v-model="sort" style="width:180px" aria-label="病灶排序" :options="[{value:'volume',label:'按体积（大到小）'},{value:'slice',label:'按层面（小到大）'}]" /></div>
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
            <section v-show="activeTab==='insights'" id="work-panel-insights" role="tabpanel" aria-labelledby="work-tab-insights">
              <CTReferencePanel v-if="resultTaskId && study?.modality === 'CT'" :key="'ct-reference-'+resultTaskId"
                :task-id="resultTaskId" @locate="locateReferenceMeasurement" />
              <AIInsights v-if="resultTaskId && study?.modality === 'CT'" :key="resultTaskId" :task-id="resultTaskId" @locate="locateInsightLesion" @locate-trial="locateChestTrial" />
            </section>
          </template>
          <div v-else class="c-result-block">
            <div class="c-between"><h2>{{ taskStatus?.status==='FAILED'?'本次分析未完成':taskStatus?'分析进度':'尚未分析' }}</h2><span class="c-badge" :class="statusTone(taskStatus)">{{ statusLabel(taskStatus) }}</span></div>
            <p style="margin-top:12px">{{ taskStatus?.status==='FAILED'?'可在分析记录中查看任务状态，原始影像仍可阅览。':taskStatus?'可继续阅览原始影像，结果生成后在这里查看。':'导入已完成，可提交病灶识别与定量分析。' }}</p>
            <template v-if="isActive(taskStatus)"><div v-for="(step,index) in steps" :key="step" class="c-step" :class="{done:index<stage,current:index===stage}"><span class="c-step-number">{{ index<stage?'✓':index+1 }}</span><div><h3>{{ step }}</h3></div></div></template>
            <button v-if="!taskStatus||taskStatus.status==='FAILED'" class="c-button c-primary" style="margin-top:16px" @click="taskStatus?showDetails=true:showSubmit=true">{{ taskStatus?'查看原因并重试':'提交分析' }}</button>
            <p v-if="pollError" class="c-small" role="status" style="margin-top:12px">{{ pollError }}</p>
          </div>
        </aside></div>
      </div>
    </main>
    <AnalysisDetails v-model="showDetails" :study="study" @open="openTask" @submit="showDetails=false;showSubmit=true" />
    <ClinicalDialog v-model="showSubmit" :busy="submitting" title="提交分析" :subtitle="study?studyCode(study.id):''"><p class="c-muted">提交当前检查进行病灶识别与定量分析。已有分析任务可在“分析记录”中查看。</p><template #footer><button class="c-button" :disabled="submitting" @click="showSubmit=false">取消</button><button class="c-button c-primary" :disabled="submitting" @click="submitAnalysis">{{ submitting?'正在提交…':'确认提交' }}</button></template></ClinicalDialog>
  </ClinicalShell>
</template>
<script setup lang="ts">
import ClinicalSelect from '@/components/ClinicalSelect.vue';
import { ref,computed,watch,onMounted,onBeforeUnmount,nextTick,type CSSProperties } from 'vue';
import { useRoute,useRouter } from 'vue-router';
import { ArrowLeft,Clock,DataAnalysis,Fold } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import AIInsights from '@/components/AIInsights.vue';
import CTReferencePanel from '@/components/CTReferencePanel.vue';
import ClinicalShell from '@/components/ClinicalShell.vue';
import ClinicalDialog from '@/components/ClinicalDialog.vue';
import AnalysisDetails from '@/components/AnalysisDetails.vue';
import { readWorkspaceView } from '@/ui/workspaceNavigation';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import { sortedTasks,displayTask,runningTask,taskId,isActive,studyCode,statusLabel,statusTone,numberText,metric,locateLesion,studyHeading,type Study,type Lesion,type AnalysisTask } from '@/ui/clinical';
const route=useRoute(),router=useRouter(),auth=useAuthStore();
const studyId=computed(()=>Number(route.params.studyId)),study=ref<Study|null>(null),taskStatus=ref<AnalysisTask|null>(null),lesions=ref<Lesion[]>([]),loading=ref(true),loadError=ref(''),resultsLoaded=ref(false),resultError=ref(''),pollError=ref('');
type RelatedSeries = { id: number; label: string; slice_count: number }
const relatedSeries = ref<RelatedSeries[]>([])
const resultTaskId=ref<number|null>(null),selectedId=ref<number|null>(null),activeTab=ref('lesions'),sort=ref('volume'),collapsed=ref(false);
const maxSlices=computed(()=>Number.isInteger(study.value?.slice_count)&&study.value!.slice_count>0?study.value!.slice_count:0);
const currentSlice=ref(1),windowWidth=ref(1500),windowCenter=ref(-600),preset=ref('肺窗'),showMask=ref(true),maskOpacity=ref(50),zoom=ref(1);
const imageWidth=ref(512),imageHeight=ref(512),viewport=ref<HTMLElement|null>(null),ctCanvas=ref<HTMLCanvasElement|null>(null),maskCanvas=ref<HTMLCanvasElement|null>(null),fitWidth=ref(512),fitHeight=ref(512);
const imageLoading=ref(false),imageError=ref(''),maskError=ref(false),displayedSlice=ref<number|null>(null);
const panX=ref(0),panY=ref(0),panActive=ref(false);
let panPointer:number|null=null,panStartX=0,panStartY=0,panOriginX=0,panOriginY=0;
const selectedLesion=computed(()=>lesions.value.find(l=>l.id===selectedId.value)||null);
const selectedLocation=computed(()=>selectedLesion.value?locateLesion(study.value,selectedLesion.value):null);
const intensityUnit=computed(()=>study.value?.metadata_json?.intensity_unit==='HU'||study.value?.metadata_json?.rescale_type==='HU'?' HU':'');
const orderedLesions=computed(()=>[...lesions.value].sort((a,b)=>sort.value==='slice'?(locationOf(a)?.slice??Infinity)-(locationOf(b)?.slice??Infinity)||a.lesion_index-b.lesion_index:(b.volume_mm3??-Infinity)-(a.volume_mm3??-Infinity)||a.lesion_index-b.lesion_index));
const selectedPosition=computed(()=>orderedLesions.value.findIndex(l=>l.id===selectedId.value));
const markerLocations=computed(()=>lesions.value.flatMap(l=>{const p=locationOf(l);return p?[{id:l.id,index:l.lesion_index,slice:p.slice}]:[];}));
const stackStyle=computed<CSSProperties>(()=>({width:fitWidth.value+'px',height:fitHeight.value+'px',aspectRatio:'auto',transform:'translate('+panX.value+'px,'+panY.value+'px) scale('+zoom.value+')'}));
const selectionNote=computed(()=>!selectedLesion.value?taskStatus.value?.status==='COMPLETED'&&resultsLoaded.value?'可继续阅览全序列。':'原始影像可继续阅览。':!selectedLocation.value?'当前病灶缺少可靠定位信息。':currentSlice.value===selectedLocation.value.slice?'已定位病灶 #'+selectedLesion.value.lesion_index+' 的中心层。':'当前为邻近层，点击病灶卡片可重新定位。');
const tabs=[{id:'lesions',label:'病灶摘要'},{id:'insights',label:'AI 洞察'}];
const steps=['等待处理','病灶分析','结果整理','分析完成'];
const stage=computed(()=>taskStatus.value?.status==='QUEUED'?0:taskStatus.value?.status==='SEGMENTING'?1:2);
const showDetails=ref(false),showSubmit=ref(false),submitting=ref(false);
type ReferenceLine = [[number, number], [number, number]];
const referenceMeasurement = ref<{ taskId: number; slice: number; endpoints: ReferenceLine } | null>(null);
const activeReferenceLine = computed(() => {
  const reference = referenceMeasurement.value;
  return reference && reference.taskId === resultTaskId.value && reference.slice === displayedSlice.value
    ? reference.endpoints : null;
});
function locateReferenceMeasurement(index: number, anchor: { slice: number; endpoints_xy: ReferenceLine }) {
  const lesion = lesions.value.find(item => item.lesion_index === index);
  if (!lesion || !resultTaskId.value || !Number.isInteger(anchor.slice) || anchor.slice < 1 || anchor.slice > maxSlices.value) return;
  selectedId.value = lesion.id;
  insightAnchor.value = null;
  referenceMeasurement.value = { taskId: resultTaskId.value, slice: anchor.slice, endpoints: anchor.endpoints_xy };
  showMask.value = true;
  changeSlice(anchor.slice);
}
type InsightAnchor = {slice:number;box_xy:[number,number,number,number]};
const insightAnchor = ref<(InsightAnchor & {taskId:number;trial?:boolean}) | null>(null);
const activeInsightBox = computed(() => {
  const anchor = insightAnchor.value;
  return anchor && anchor.taskId === resultTaskId.value && anchor.slice === displayedSlice.value ? anchor.box_xy : null;
});
function openInsightAnchor(anchor:InsightAnchor, trial = false) {
  const box = anchor.box_xy;
  if (!resultTaskId.value || !Number.isInteger(anchor.slice) || anchor.slice < 1 || anchor.slice > maxSlices.value
    || !Array.isArray(box) || box.length !== 4 || !box.every(value=>Number.isFinite(value) && value>=0 && value<=1)
    || box[0]>=box[2] || box[1]>=box[3]) return;
  referenceMeasurement.value=null;
  insightAnchor.value={...anchor,taskId:resultTaskId.value,trial};
  if (trial) selectedId.value=null;
  changeSlice(anchor.slice);
}
function locateChestTrial(anchor:InsightAnchor) { openInsightAnchor(anchor, true); }
function locateInsightLesion(id:string, anchor?:InsightAnchor) {
  const index = Number(id.replace(/^L/,''));
  const lesion = lesions.value.find(item=>item.lesion_index===index);
  if (!lesion) return;
  if (!anchor) { selectLesion(lesion.id); return; }
  selectedId.value=lesion.id;
  openInsightAnchor(anchor);
}
let disposed=false,loadGeneration=0,renderGeneration=0,resultGeneration=0,pollTimer:ReturnType<typeof setTimeout>|null=null,observer:ResizeObserver|null=null,renderController:AbortController|null=null;
let warmController:AbortController|null=null;
const imageCache=new Map<string,ImageBitmap>();
function cachePut(key:string,image:ImageBitmap){imageCache.set(key,image);if(imageCache.size>40){const oldest=imageCache.keys().next().value;if(oldest){imageCache.get(oldest)?.close();imageCache.delete(oldest);}}}
function clearCache(){imageCache.forEach(image=>image.close());imageCache.clear();}
function resize(){const v=viewport.value;if(!v)return;const ratio=imageWidth.value/imageHeight.value;fitWidth.value=Math.min(v.clientWidth,v.clientHeight*ratio);fitHeight.value=fitWidth.value/ratio;}
function locationOf(lesion:Lesion){return locateLesion(study.value,lesion);}
function selectLesion(id:number){insightAnchor.value=null;referenceMeasurement.value=null;selectedId.value=id;const lesion=lesions.value.find(l=>l.id===id);const point=lesion?locationOf(lesion):null;if(point)changeSlice(point.slice);}
function selectAdjacent(delta:number){const item=orderedLesions.value[selectedPosition.value+delta];if(item)selectLesion(item.id);}
function changeSlice(value:number){if(!maxSlices.value)return;currentSlice.value=Math.max(1,Math.min(maxSlices.value,Math.round(value)||1));void renderCurrent();}
function viewportKey(event:KeyboardEvent){if(event.target!==event.currentTarget||!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();changeSlice(event.key==='Home'?1:event.key==='End'?maxSlices.value:currentSlice.value+(event.key==='ArrowDown'?1:-1));}
function wheel(event:WheelEvent){
 if(!event.deltaY)return;
 if(event.ctrlKey){zoom.value=Math.max(.6,Math.min(2,Math.round((zoom.value+(event.deltaY<0?.1:-.1))*100)/100));return;}
 if(!maxSlices.value)return;changeSlice(currentSlice.value+(event.deltaY>0?1:-1));
}
function resetView(){zoom.value=1;panX.value=0;panY.value=0;}
function startPan(event:PointerEvent){
 if(!event.ctrlKey||event.button!==0||panPointer!==null)return;
 event.preventDefault();viewport.value?.focus({preventScroll:true});
 panPointer=event.pointerId;panStartX=event.clientX;panStartY=event.clientY;panOriginX=panX.value;panOriginY=panY.value;panActive.value=true;
 viewport.value?.setPointerCapture(event.pointerId);
}
function movePan(event:PointerEvent){
 if(event.pointerId!==panPointer)return;
 if(!(event.buttons&1)||!event.ctrlKey){endPan(event);return;}
 event.preventDefault();panX.value=panOriginX+event.clientX-panStartX;panY.value=panOriginY+event.clientY-panStartY;
}
function endPan(event:PointerEvent){
 if(event.pointerId!==panPointer)return;
 panPointer=null;panActive.value=false;
 if(viewport.value?.hasPointerCapture(event.pointerId))viewport.value.releasePointerCapture(event.pointerId);
}
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
 imageLoading.value=true;imageError.value='';maskError.value=false;
 try{
  const image=await readImage(imageUrl(slice),signal);if(version!==renderGeneration||studyVersion!==loadGeneration||disposed)return;
  // Resize backing stores only within this synchronous commit; resizing clears a canvas.
  for(const canvas of [ctCanvas.value,maskCanvas.value]){if(canvas){if(canvas.width!==image.width)canvas.width=image.width;if(canvas.height!==image.height)canvas.height=image.height;}}
  imageWidth.value=image.width;imageHeight.value=image.height;
  // Commit only a decoded current image. Keep the previous image and its overlay while loading.
  resize();clearCanvas(maskCanvas.value);ctCanvas.value?.getContext('2d')?.drawImage(image,0,0,image.width,image.height);displayedSlice.value=slice;imageLoading.value=false;
  if(resultTaskId.value&&lesions.value.length){
   try{const mask=await readImage(imageUrl(slice,true),signal);if(version!==renderGeneration||studyVersion!==loadGeneration||disposed)return;if(mask.width!==image.width||mask.height!==image.height){maskError.value=true;return;}maskCanvas.value?.getContext('2d')?.drawImage(mask,0,0,image.width,image.height);}
   catch(error){if((error as Error).name!=='AbortError'&&version===renderGeneration)maskError.value=true;}
  }
  if(version===renderGeneration)warmNeighbors(slice,studyVersion);
 }catch(error){if((error as Error).name!=='AbortError'&&version===renderGeneration){imageError.value=displayedSlice.value?'第 '+slice+' 层读取失败，仍显示第 '+displayedSlice.value+' 层，请重试切换。':'该切片读取失败，请切换层面或重新读取检查。';imageLoading.value=false;}}
}
async function loadResults(){
 const id=taskId(taskStatus.value);if(!id||taskStatus.value?.status!=='COMPLETED')return;
 const version=++resultGeneration,studyVersion=loadGeneration;resultError.value='';resultsLoaded.value=false;
 try{const result:any=await api.get('/results/'+id);if(disposed||studyVersion!==loadGeneration||version!==resultGeneration)return;lesions.value=Array.isArray(result.lesions)?result.lesions:[];resultTaskId.value=id;resultsLoaded.value=true;selectedId.value=orderedLesions.value[0]?.id||null;const point=selectedLesion.value?locationOf(selectedLesion.value):null;if(point)currentSlice.value=point.slice;restoreWorkspaceView();await renderCurrent();}
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
async function loadRelatedSeries(id: number, version: number) {
  try {
    const response = await api.get('/studies/' + id + '/series')
    if (!disposed && version === loadGeneration) relatedSeries.value = response as unknown as RelatedSeries[]
  } catch {
    if (version === loadGeneration) relatedSeries.value = []
  }
}
function openSeries(event: Event) {
  const id = Number((event.target as HTMLSelectElement).value)
  if (id !== studyId.value) void router.push('/workspace/' + id)
}
async function loadStudy(){
 relatedSeries.value = []
 activeTab.value = 'lesions'

 const version=++loadGeneration;resultGeneration++;if(pollTimer)clearTimeout(pollTimer);warmController?.abort();renderController?.abort();renderGeneration++;clearCache();loading.value=true;loadError.value='';pollError.value='';resultsLoaded.value=false;resultError.value='';study.value=null;taskStatus.value=null;lesions.value=[];selectedId.value=null;resultTaskId.value=null;referenceMeasurement.value=null;currentSlice.value=1;displayedSlice.value=null;imageLoading.value=false;imageError.value='';maskError.value=false;resetView();clearCanvas(ctCanvas.value);clearCanvas(maskCanvas.value);
 if(!Number.isInteger(studyId.value)||studyId.value<1){loadError.value='检查编号无效。';loading.value=false;return;}
 try{const data:any=await api.get('/studies/'+studyId.value);if(disposed||version!==loadGeneration)return;study.value=data;
  if (data.modality === 'CT') void resolveIntensityUnit(data,version);
  if (data.metadata_json?.source_study_instance_uid) void loadRelatedSeries(data.id, version);
  const requested=Number(route.query.task);const tasks=sortedTasks(data);taskStatus.value=tasks.find(t=>taskId(t)===requested)||displayTask(data);currentSlice.value=Math.max(1,Math.round(maxSlices.value/2));zoom.value=1;
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

const pageVisible = ref(false);
const resultPanel = ref<HTMLElement | null>(null);
const resultInViewport = ref(false);
let resultObserver: IntersectionObserver | undefined;
let workspaceReported = false;
let viewedTaskId: number | null = null;

function syncPageVisibility() {
  pageVisible.value = document.visibilityState === 'visible';
}

function toggleResults() {
  collapsed.value = !collapsed.value;
  if (!collapsed.value) viewedTaskId = null;
}

function reportActivity(activity: { kind: 'workspace_opened' } | { kind: 'results_viewed'; task_id: number }) {
  void api.post('/studies/'+studyId.value+'/activity', activity, {
    timeout: 5000,
    suppressErrorToast: true,
  }).catch(() => {
    // Activity tracking must not interrupt the viewer.
  });
}

watch(
  [() => route.params.studyId, () => route.query.task],
  ([nextStudyId], [previousStudyId]) => {
    if (nextStudyId !== previousStudyId) workspaceReported = false;
    viewedTaskId = null;
    if (!disposed) void loadStudy();
  },
);

watch([study, studyId, pageVisible, resultInViewport, resultsLoaded, resultTaskId, collapsed], () => {
  if (disposed || !pageVisible.value || study.value?.id !== studyId.value) return;
  if (!workspaceReported) {
    workspaceReported = true;
    reportActivity({ kind: 'workspace_opened' });
  }

  const task = resultTaskId.value;
  if (collapsed.value || !resultInViewport.value || !resultsLoaded.value || task === null || task === viewedTaskId) return;
  viewedTaskId = task;
  reportActivity({ kind: 'results_viewed', task_id: task });
}, { flush: 'post' });

onMounted(()=>{
  document.addEventListener('visibilitychange', syncPageVisibility);
  syncPageVisibility();
  if (resultPanel.value) {
    resultObserver = new IntersectionObserver(([entry]) => {
      resultInViewport.value = entry.isIntersecting;
    });
    resultObserver.observe(resultPanel.value);
  }
  observer=new ResizeObserver(resize);
  if(viewport.value)observer.observe(viewport.value);
  void loadStudy();
});
onBeforeUnmount(()=>{
  disposed=true;
  document.removeEventListener('visibilitychange', syncPageVisibility);
  resultObserver?.disconnect();
  loadGeneration++;resultGeneration++;renderGeneration++;
  if(pollTimer)clearTimeout(pollTimer);
  observer?.disconnect();warmController?.abort();renderController?.abort();clearCache();
});
</script>

<style scoped>
.c-viewport{user-select:none;touch-action:none}
.c-viewport.is-panning{cursor:grabbing}
.c-feature-entry{margin-bottom:22px}
.c-feature-entry .c-feature-action{width:100%;justify-content:center;padding:11px 16px;font-size:15px}
.clinical-ui .c-feature-action{min-height:44px;background:#487fbd;color:#fff;border-color:#487fbd;font-weight:600}
.clinical-ui .c-feature-action:hover:not(:disabled){background:#3c6da5;border-color:#3c6da5}
</style>
