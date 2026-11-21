<template>
  <ClinicalShell><div class="c-radiomics">

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-1 w-full space-y-6">
      <div class="c-pagehead">
        <div><router-link :to="backTarget" class="c-link c-back-link"><el-icon><ArrowLeft /></el-icon>{{ fromWorkspace ? '返回影像' : '检查列表' }}</router-link><h1 style="margin-top:8px">特征计算与数据导出</h1></div>
      </div>
      <!-- Section 1: Study Scope Selector -->
      <section class="bg-white rounded-xl border border-slate-200/80 p-5 shadow-sm">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <div>
            <span class="text-xs font-bold text-blue-600 uppercase tracking-wider font-mono">STEP 01</span>
            <h2 class="text-base font-bold text-slate-900 mt-0.5">{{ fromWorkspace ? '当前检查与计算范围' : '选择目标患者检查与病灶数据' }}</h2>
          </div>

        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-slate-600 mb-1.5">检查记录选择</label>
            <el-select
              v-model="selectedStudyId"
              popper-class="clinical-select-popper"
              :disabled="busy || loadingStudy"
              placeholder="请选择检查病例..."
              class="w-full !rounded-lg"
              size="large"
              @change="onStudyChange"
            >
              <el-option
                v-for="s in studies"
                :key="s.id"
                :label="`${s.patient?.patient_id || '未提供患者编号'}-${s.study_description || '未提供检查描述'}[${s.slice_count}层]`"
                :value="s.id"
              />
            </el-select>
          </div>

          <div class="bg-slate-50 rounded-xl p-3.5 border border-slate-200/80 flex items-center justify-around text-center">
            <div>
              <span class="text-[10px] text-slate-400 block uppercase font-mono">检出病灶</span>
              <strong class="text-lg font-bold text-slate-800 font-mono">{{ activeLesions.length }}</strong>
            </div>
            <div class="h-8 w-px bg-slate-200"></div>
            <div>
              <span class="text-[10px] text-slate-400 block uppercase font-mono">切片层数</span>
              <strong class="text-lg font-bold text-blue-600 font-mono">{{ currentStudy?.slice_count || 0 }}</strong>
            </div>
            <div class="h-8 w-px bg-slate-200"></div>
            <div>
              <span class="text-[10px] text-slate-400 block uppercase font-mono">任务状态</span>
              <el-tag size="small" :type="activeTaskId ? 'success' : 'info'" class="mt-1">
                {{ loadingStudy?'读取中':studyError?'读取失败':activeTaskId ? '已完成分析' : '暂无已完成分析' }}
              </el-tag>
            </div>
          </div>
        </div>
        <p v-if="activeTaskId" class="c-small c-muted" style="margin-top:14px" role="status">{{ ['MR', 'MRI'].includes(currentStudy?.modality ?? '') ? `本次分析有 ${activeLesions.length} 个候选病灶。特征计算使用通过输入校验的 3D ROI。` : `重新提取与导出使用本次分析的全部 ${activeLesions.length} 个病灶。` }}</p>
      </section>

      <p v-if="studyError" class="c-alert" role="alert">{{ studyError }} <button class="c-link" @click="selectedStudyId?onStudyChange(selectedStudyId):fetchStudies()">重试</button></p>
      <p v-if="catalogError" class="c-alert" role="alert">{{ catalogError }} <button class="c-link" @click="activeTaskId&&loadCatalog(activeTaskId)">重新加载目录</button></p><p v-if="catalogLoading" class="c-muted c-small" role="status">正在加载在线特征目录…</p>
      <section class="review-section" aria-labelledby="target-heading">
        <header class="review-heading"><div><span class="review-step">STEP 02 / 目标特征</span><h2 id="target-heading">选择目标特征</h2></div><button type="button" class="c-button" :aria-expanded="!featuresCollapsed" aria-controls="target-content" @click="featuresCollapsed=!featuresCollapsed">{{ featuresCollapsed?'展开配置':'收起配置' }}</button></header>
        <div id="target-content" v-show="!featuresCollapsed"><FeatureTargetPicker :model-value="draft.selectedKeys" :catalog="catalog" @update:model-value="changeTargets" /></div>
      </section>
      <section class="review-section" aria-labelledby="processing-heading">
        <header class="review-heading"><div><span class="review-step">STEP 03 / 处理方案</span><h2 id="processing-heading">参数设置与输入依赖</h2></div><div class="c-row"><button type="button" class="c-button" @click="resetProcessing">恢复默认参数</button><button type="button" class="c-button" :aria-expanded="!processingCollapsed" aria-controls="processing-content" @click="processingCollapsed=!processingCollapsed">{{ processingCollapsed?'展开配置':'收起配置' }}</button></div></header>
        <div id="processing-content" v-show="!processingCollapsed"><ProcessingSettings :key="processingRevision" v-model="config" v-model:draft="draft" :catalog="catalog" :modalities="currentStudy?.modality?[currentStudy.modality]:[]" :scope-filter="{studyId:selectedStudyId,taskId:activeTaskId}" /></div>
      </section>
      <section id="review-submit" class="review-section" aria-labelledby="submit-heading">
        <header class="review-heading"><div><span class="review-step">STEP 04 / 检查与提交</span><h2 id="submit-heading">检查当前输入与提取方案</h2></div><span class="c-badge" :class="previewIsCurrent?'active':'warning'">{{ previewIsCurrent?'输入已确认':hadPreview?'配置已变化，需重新检查':'尚未检查输入' }}</span></header>
        <p v-if="previewIsCurrent && confirmation?.excluded_lesions?.length" class="c-alert review-error" role="status">
          {{ confirmation.excluded_lesions.length }} 个单层候选不参与 3D 特征计算。
        </p>
        <p v-if="previewError" class="c-alert review-error" role="alert">{{ previewError }}</p>
        <h3 class="summary-title">提取方案摘要</h3>
        <dl class="review-summary">
          <div><dt>数据范围</dt><dd>{{ selectedStudyId?`检查 #${selectedStudyId}`:'尚未选择' }} · {{ activeTaskId?`分析任务 #${activeTaskId}`:'暂无已完成分析' }} · 全部 {{ activeLesions.length }} 个病灶</dd></div>
          <div><dt>目标特征</dt><dd>{{ dependencies.count }} 项 · {{ dependencies.families.map(f=>familyAbbreviations[f.id]).join(' / ')||'尚未选择' }}</dd></div>
          <div><dt>源影像读取</dt><dd>{{ config.source_version==='physical-v1'?'按源文件读取空间信息与强度':'历史兼容读取' }}</dd></div>
          <div><dt>网格与插值</dt><dd>{{ gridLabel }} · 影像：{{ config.resampling==='none'?'不适用':interpolationLabel }} · ROI：{{ config.resampling==='none'?'不适用':draft.maskInterpolator==='sitkLinear'?`Trilinear，阈值 ${draft.maskThreshold}`:'Nearest Neighbor' }}</dd></div>
          <div><dt>掩膜与邻域依赖</dt><dd>{{ [dependencies.morphology?'形态学掩膜':'',dependencies.intensity?'强度掩膜':'',dependencies.localContext?'保留 ROI 外邻域':''].filter(Boolean).join(' + ')||'尚未选择' }}</dd></div>
          <div><dt>强度重分割</dt><dd>{{ draft.resegmentation?`[${draft.intensityMin}, ${draft.intensityMax}] · ${draft.intensityUnit||'单位待填写'}（按声明与源文件核验）`:'关闭' }}</dd></div>
          <div><dt>归一化</dt><dd>{{ normalizationLabel }}</dd></div>
          <div><dt>离散化</dt><dd>{{ dependencies.discretisation?discretisationSummary(config,draft.selectedKeys,catalog):'当前目标无需离散化' }}</dd></div>
          <div><dt>肝脏补充特征</dt><dd>{{ config.include_liver_features?'适用模态追加（不计入 198 项目标目录）':'关闭' }}</dd></div>
        </dl>
        <p v-if="runError" class="c-alert review-error" role="alert">{{ runError }}</p>
      </section>
      <CalculationHistory :task-id="activeTaskId" :submitted-run="submittedRun" @latest-run="currentRun=$event" @loading="historyLoading=$event" />
    </main>
    <footer class="c-export-bar"><div><strong>已选 {{ dependencies.count }} 项 · {{ dependencies.families.length }} 类特征</strong><p class="c-small c-muted" role="status">{{ actionHint }}</p></div><div class="c-export-actions"><button type="button" class="c-button" :disabled="loadingStudy||isChecking||!activeTaskId||errors.length>0||!catalogVersion||busy" :aria-busy="isChecking" @click="checkInputs">{{ isChecking?'正在核验输入…':'检查配置与输入' }}</button><button type="button" class="c-button c-primary" :disabled="blockers.length>0||isChecking||busy" @click="submitCalculation">{{ submitting?'提交中…':busy?'计算中…':'开始计算' }}</button></div></footer>
  </div></ClinicalShell>
</template>

<script setup lang="ts">
import '@/features/radiomics/interactions.css'
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import api from '@/api/client'
import ClinicalShell from '@/components/ClinicalShell.vue'
import { sortedTasks, taskId } from '@/ui/clinical'
import { positiveId } from '@/ui/workspaceNavigation'
import FeatureTargetPicker from '@/features/radiomics/FeatureTargetPicker.vue'
import ProcessingSettings from '@/features/radiomics/ProcessingSettings.vue'
import CalculationHistory from '@/features/radiomics/CalculationHistory.vue'
import type { CalculationRun } from '@/features/radiomics/history'
import { radiomicsCatalog, createPlanDraft, defaultRadiomicsConfig, planDependencies, derivePyradiomicsClasses, discretisationSummary, executableConfig } from '@/features/radiomics/plan'
import { validationErrors, reviewFingerprint } from '@/features/radiomics/review'
import type { RadiomicsFeatureDefinition } from '@/features/radiomics/types'
const route=useRoute()
const fromWorkspace=computed(()=>route.query.from==='workspace'&&positiveId(route.query.study)!==null)
const backTarget=computed(()=>fromWorkspace.value?{name:'MedicalWorkspace',params:{studyId:String(positiveId(route.query.study))},query:positiveId(route.query.task)?{task:String(positiveId(route.query.task))}:{},state:{workspaceView:window.history.state?.workspaceView??null}}:{name:'StudyDashboard'})
const studies=ref<any[]>([]),selectedStudyId=ref<number|null>(null),currentStudy=ref<any>(null),activeTaskId=ref<number|null>(null),activeLesions=ref<any[]>([])
const loadingStudy=ref(false),studyError=ref('')
const submitting=ref(false),catalogLoading=ref(false),catalogError=ref(''),catalogVersion=ref(''),confirmation=ref<any>(null),currentRun=ref<CalculationRun|null>(null),runError=ref('')
const submittedRun=ref<CalculationRun|null>(null),historyLoading=ref(false)
const busy=computed(()=>submitting.value||historyLoading.value||['pending','running'].includes(currentRun.value?.status||''))
let studyGeneration=0,listGeneration=0
const catalog=ref<RadiomicsFeatureDefinition[]>([])
const config=ref(defaultRadiomicsConfig()),draft=ref(createPlanDraft(radiomicsCatalog))
const featuresCollapsed=ref(false),processingCollapsed=ref(false),processingRevision=ref(0)
const dependencies=computed(()=>planDependencies(draft.value.selectedKeys,catalog.value))
const familyAbbreviations:Record<string,string>={morphology:'MORPH',local_intensity:'LI',intensity_statistics:'STAT',intensity_histogram:'IH',intensity_volume_histogram:'IVH',glcm:'GLCM',glrlm:'GLRLM',glszm:'GLSZM',gldzm:'GLDZM',ngtdm:'NGTDM',ngldm:'NGLDM',custom_lung_nodule:'CUSTOM'}

watch([()=>draft.value.selectedKeys,catalogVersion],([keys])=>{config.value.features={catalog_version:catalogVersion.value,keys:[...keys]};config.value.feature_classes=derivePyradiomicsClasses(keys,catalog.value)},{deep:true,immediate:true,flush:'sync'})
function resetProcessing(){config.value=defaultRadiomicsConfig();config.value.features={catalog_version:catalogVersion.value,keys:[...draft.value.selectedKeys]};config.value.feature_classes=derivePyradiomicsClasses(draft.value.selectedKeys,catalog.value);draft.value={...createPlanDraft(catalog.value),selectedKeys:[...draft.value.selectedKeys]};processingRevision.value++}
function changeTargets(keys:string[]){if(JSON.stringify([...keys].sort())===JSON.stringify([...draft.value.selectedKeys].sort()))return;draft.value.selectedKeys=[...keys];resetProcessing()}
const errors=computed(()=>validationErrors(config.value,draft.value,catalog.value,currentStudy.value?.modality??''))
const scope=computed(()=>({study_id:selectedStudyId.value,task_id:activeTaskId.value,lesions:activeLesions.value.map(l=>({id:l.id,index:l.lesion_index,mask:l.mask_path,updated_at:l.updated_at}))}))
const fingerprint=computed(()=>reviewFingerprint(scope.value,config.value,draft.value))
const checkedFingerprint=ref(''),hadPreview=ref(false),isChecking=ref(false),previewError=ref('')
const previewIsCurrent=computed(()=>!!checkedFingerprint.value&&checkedFingerprint.value===fingerprint.value)
watch(fingerprint,()=>{checkedFingerprint.value='';confirmation.value=null;previewError.value=''},{flush:'sync'})
const blockers=computed(()=>[...errors.value,...(loadingStudy.value?['正在读取检查结果']:studyError.value?[studyError.value]:!activeTaskId.value?['请选择已完成分析的检查']:!activeLesions.value.length?['当前分析没有可计算的病灶 ROI']:[]),...(!previewIsCurrent.value?['请先检查当前配置与输入']:[]),...(!catalogVersion.value?['在线特征目录尚未加载']:[]),...(busy.value?['当前计算正在执行，请等待完成']:[])])
const actionHint=computed(()=>isChecking.value?'正在核验源影像、掩膜与配置…':historyLoading.value?'正在读取计算记录…':busy.value?'当前计算正在执行，请等待完成':errors.value[0]||studyError.value||(loadingStudy.value?'正在读取检查结果':!activeTaskId.value?'请选择已完成分析的检查':!catalogVersion.value?'在线特征目录尚未加载':previewError.value||(!previewIsCurrent.value?'确认参数后，检查当前配置与输入':'输入已确认，可以开始计算')))
const gridLabel=computed(()=>config.value.resampling==='none'?'原生网格':config.value.resampling.join(' × ')+' mm')
const interpolationLabel=computed(()=>({sitkBSpline:'B-Spline',sitkLinear:'Trilinear',sitkNearestNeighbor:'Nearest Neighbor'})[config.value.image_interpolator??'sitkBSpline'])
const normalizationLabel=computed(()=>!dependencies.value.intensity?'当前目标无需强度归一化':config.value.normalization==='none'?'不归一化（倍率不适用）':config.value.normalization==='auto'?`按模态自适应：MR Z-Score × ${config.value.normalization_scale??100}，CT 不归一化`:`Z-Score × ${config.value.normalization_scale??100}`)
async function fetchStudies(){
 const generation=++listGeneration
 try{
  loadingStudy.value=true;studyError.value=''
  const res:any=await api.get('/studies');if(generation!==listGeneration)return
  studies.value=Array.isArray(res)?res:[]
  const requested=positiveId(route.query.study)
  if(route.query.study!==undefined&&!requested){studyGeneration++;activeTaskId.value=null;activeLesions.value=[];selectedStudyId.value=null;currentStudy.value=null;studyError.value='检查编号无效，请返回后重新选择。';return}
  if(requested){let target=studies.value.find(s=>s.id===requested);if(!target){target=await api.get('/studies/'+requested);if(generation!==listGeneration)return;studies.value.push(target)}selectedStudyId.value=requested;await onStudyChange(requested)}
  else if(studies.value.length){selectedStudyId.value=(studies.value.find(s=>sortedTasks(s).some(t=>t.status==='COMPLETED'))||studies.value[0]).id;await onStudyChange(selectedStudyId.value!)}
  else{studyGeneration++;activeTaskId.value=null;activeLesions.value=[];selectedStudyId.value=null;currentStudy.value=null}
 }catch{if(generation===listGeneration){studyGeneration++;activeTaskId.value=null;activeLesions.value=[];studyError.value='检查列表读取失败，请重试。'}}
 finally{if(generation===listGeneration)loadingStudy.value=false}
}
async function onStudyChange(studyId:number){
 const generation=++studyGeneration
 currentStudy.value=studies.value.find(s=>s.id===studyId);activeTaskId.value=null;activeLesions.value=[];currentRun.value=null;submittedRun.value=null;runError.value='';studyError.value='';checkedFingerprint.value='';previewError.value=''
 const requested=positiveId(route.query.task),useRequestedTask=studyId===positiveId(route.query.study)&&route.query.task!==undefined
 const completed=sortedTasks(currentStudy.value).find(t=>t.status==='COMPLETED'&&(!useRequestedTask||taskId(t)===requested))
 if(useRequestedTask&&!completed){loadingStudy.value=false;studyError.value='指定分析任务不存在或尚未完成，请返回影像检查后重试。';return}
 const id=taskId(completed);loadingStudy.value=!!id;if(!id)return
 try{const results:any=await api.get('/results/'+id);if(generation!==studyGeneration)return;activeLesions.value=Array.isArray(results?.lesions)?results.lesions:[];activeTaskId.value=id;await loadCatalog(id,generation)}
 catch{if(generation===studyGeneration)studyError.value='结果读取失败，请重试。'}
 finally{if(generation===studyGeneration)loadingStudy.value=false}
}
function endpoint(id:number){return '/results/'+id+'/feature-calculation'}
function requestedConfig(){return executableConfig(config.value,draft.value)}
async function loadCatalog(id:number,generation=studyGeneration){
 catalogLoading.value=true;catalogError.value=''
 try{const response:any=await api.get(endpoint(id)+'/catalog');if(generation!==studyGeneration)return
 if(response.total_features_count!==198||response.features?.length!==198)throw new Error('特征目录不完整')
 catalog.value=[...response.features,...radiomicsCatalog.filter(f=>f.status!=='computed')];catalogVersion.value=response.catalog_version
 }catch(e:any){if(generation===studyGeneration){catalogVersion.value='';catalogError.value=e.message||'目录读取失败'}}finally{if(generation===studyGeneration)catalogLoading.value=false}
}
async function checkInputs(){
 if(isChecking.value||loadingStudy.value||!activeTaskId.value||errors.value.length||!catalogVersion.value||busy.value)return
 const id=activeTaskId.value,generation=studyGeneration,requestedFingerprint=fingerprint.value
 isChecking.value=true;previewError.value='';checkedFingerprint.value='';confirmation.value=null
 try{const result:any=await api.post(endpoint(id)+'/confirm',{config:requestedConfig()})
 if(generation!==studyGeneration||id!==activeTaskId.value||requestedFingerprint!==fingerprint.value)return
 if(!result.snapshot_token||!result.input_hash||!result.configuration_hash||!result.lesion_count)throw new Error('输入确认响应不完整')
 confirmation.value=result;checkedFingerprint.value=fingerprint.value;hadPreview.value=true
 }catch(e:any){if(generation===studyGeneration&&requestedFingerprint===fingerprint.value)previewError.value=e.message||'输入确认失败，请重试。'}finally{isChecking.value=false}
}
async function submitCalculation(){
 if(!activeTaskId.value||!previewIsCurrent.value||!confirmation.value||blockers.value.length||busy.value)return
 const id=activeTaskId.value,generation=studyGeneration;submitting.value=true;runError.value=''
 try{const run:any=await api.post(endpoint(id)+'/runs',{config:requestedConfig(),snapshot_token:confirmation.value.snapshot_token});if(generation!==studyGeneration)return;currentRun.value=run;submittedRun.value=run;checkedFingerprint.value='';confirmation.value=null}
 catch(e:any){if(generation===studyGeneration){runError.value=e.message||'提交失败';if(e.response?.status===409){checkedFingerprint.value='';confirmation.value=null}}}finally{submitting.value=false}
}
onMounted(()=>{void fetchStudies()})
onBeforeUnmount(()=>{studyGeneration++})
watch(()=>[route.query.study,route.query.task],()=>{studyGeneration++;activeTaskId.value=null;activeLesions.value=[];void fetchStudies()})
</script>
<style scoped>
.review-section{padding:20px;border:1px solid var(--c-line);border-radius:12px;background:var(--c-panel);box-shadow:0 1px 2px #00000005}.review-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--c-line);padding-bottom:13px;margin-bottom:17px}.review-step{font-size:11px;letter-spacing:.8px;font-weight:700;color:var(--c-blue);font-family:monospace}.review-heading h2{font-size:16px;margin-top:3px}.migration-note{font-size:12px;padding:12px 15px;border:1px solid var(--c-line);border-radius:8px;background:var(--c-blue-soft);color:var(--c-blue);line-height:1.7}.summary-title{margin:0 0 8px!important;font-size:14px}.review-error{margin:0 0 14px}.review-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 26px}.review-summary>div{min-width:0;padding:12px 0;border-bottom:1px solid var(--c-line)}.review-summary dt{font-size:11px;font-weight:500;color:var(--c-muted);margin-bottom:5px}.review-summary dd{font-size:13px;font-weight:600;overflow-wrap:anywhere;line-height:1.7}.c-export-bar{z-index:20}@media(max-width:700px){.review-summary{grid-template-columns:1fr}.review-section{padding:16px}.review-heading{align-items:flex-start}}
.review-heading .c-button:not(:disabled):hover{background:var(--c-blue-soft);border-color:var(--c-line);color:var(--c-blue)}
</style>
