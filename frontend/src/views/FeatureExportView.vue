<template>
  <ClinicalShell><div class="c-radiomics">

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-1 w-full space-y-6">
      <div class="c-pagehead">
        <div><router-link :to="backTarget" class="c-link"><el-icon><ArrowLeft /></el-icon>{{ fromWorkspace ? '返回影像' : '检查列表' }}</router-link><h1 style="margin-top:8px">特征计算与数据导出</h1></div>
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
        <p v-if="activeTaskId" class="c-small c-muted" style="margin-top:14px" role="status">计算范围：本次分析的全部 {{ activeLesions.length }} 个病灶。重新提取与导出均使用此范围。</p>
      </section>

      <p v-if="studyError" class="c-alert" role="alert">{{ studyError }} <button class="c-link" @click="selectedStudyId?onStudyChange(selectedStudyId):fetchStudies()">重试</button></p>
      <!-- Section 2: Presets Selection -->
      <section class="bg-white rounded-xl border border-slate-200/80 p-5 shadow-sm">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <div>
            <span class="text-xs font-bold text-blue-600 uppercase tracking-wider font-mono">STEP 02</span>
            <h2 class="text-base font-bold text-slate-900 mt-0.5">预设计算方案</h2>
          </div>

        </div>

        <RadiomicsPresetCards :presets="presets" :active-preset-id="activePresetId" :busy="busy" @apply="applyPreset" />
      </section>

      <p v-if="!families.length" class="c-muted c-small" role="status">{{ loadingStudy?'正在读取特征目录…':'选择有已完成分析的检查后，将显示可用预设与特征。' }}</p>
      <!-- Section 3: Feature Families Picker -->
      <section class="bg-white rounded-xl border border-slate-200/80 p-5 shadow-sm">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <div>
            <span class="text-xs font-bold text-blue-600 uppercase tracking-wider font-mono">STEP 03</span>
            <h2 class="text-base font-bold text-slate-900 mt-0.5">目标特征家族 ({{ selectedFamilies.length }} / {{ families.length }} 类已选)</h2>
          </div>
          <div class="flex items-center space-x-3 text-xs">
            <button type="button" class="text-blue-600 hover:underline font-medium" @click="selectAllFamilies(true)">全选所有家族</button>
            <span class="text-slate-300">|</span>
            <button type="button" class="text-slate-500 hover:underline" @click="selectAllFamilies(false)">清空勾选</button>
          </div>
        </div>

        <RadiomicsFamilyCards :families="families" :selected-families="selectedFamilies" @toggle="toggleFamily" />
      </section>

      <!-- Section 4: IBSI Processing Protocol -->
      <section class="bg-white rounded-xl border border-slate-200/80 p-5 shadow-sm">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
          <div>
            <span class="text-xs font-bold text-blue-600 uppercase tracking-wider font-mono">STEP 04</span>
            <h2 class="text-base font-bold text-slate-900 mt-0.5">参数设置</h2>
          </div>

        </div>

        <RadiomicsProcessingFields :form="form" />
      </section>

      <!-- Section 5: Matrix Preview & Action Bar -->
      <section class="bg-white rounded-xl border border-slate-200/80 p-5 shadow-sm space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-100">
          <div>
            <span class="text-xs font-bold text-blue-600 uppercase tracking-wider font-mono">STEP 05</span>
            <h2 class="text-base font-bold text-slate-900 mt-0.5">特征计算</h2>
          </div>


        </div>

        <!-- Table Preview -->
        <div v-if="activeLesions.length > 0" class="border rounded-xl overflow-hidden">
          <el-table :data="tablePreviewData" stripe style="width: 100%" size="small">
            <el-table-column prop="lesion_id" label="病灶序号" width="90" align="center">
              <template #default="{ row }">
                <span class="font-bold font-mono text-blue-600">#{{ row.lesion_id }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="density_type" label="密度分型" min-width="160" />
            <el-table-column prop="volume_mm3" label="体积 (mm³)" min-width="120" />
            <el-table-column prop="max_diameter_mm" label="最大3D长径 (mm)" min-width="130" />
            <el-table-column prop="mean_hu" label="平均CT值 (HU)" min-width="120" />
            <el-table-column prop="sphericity" label="球形度" min-width="100" />
            <el-table-column prop="entropy" label="一阶熵" min-width="100" />
            <el-table-column prop="glcm_contrast" label="GLCM对比度" min-width="120" />
          </el-table>
        </div>

        <div v-else class="text-center py-12 text-slate-400 bg-slate-50/50 rounded-xl border border-dashed border-slate-200">
          <el-icon :size="36" class="mb-2 text-slate-300"><DocumentDelete /></el-icon>
          <p class="text-xs">暂无病灶与组学特征数据</p>
          <p class="text-[11px] text-slate-400 mt-1">请选择已完成智能分析的检查，或先进入三维工作台发起分析</p>
        </div>
      </section>
    </main><footer class="c-export-bar"><div><strong>已选 {{ selectedFamilies.length }} 类特征</strong><p class="c-small c-muted" role="status">{{ busy?'正在处理，请稍候…':loadingStudy?'正在读取检查结果…':studyError?'检查结果未就绪，请重试':!activeTaskId?'请先选择已完成分析的检查':!selectedFamilies.length?'请至少选择一类特征':'确认参数后，可重新提取或导出数据' }}</p></div>          <div class="c-export-actions">
            <el-button
              size="default"
              :loading="recalculating"
              :disabled="busy || loadingStudy || !activeTaskId || selectedFamilies.length === 0"
              @click="handleRecalculate"
            >
              <el-icon class="mr-1"><Refresh /></el-icon>
              按当前参数重新提取
            </el-button>

            <el-button
              size="default"
              :loading="exportingCsv"
              :disabled="busy || loadingStudy || !activeTaskId || selectedFamilies.length === 0"
              @click="handleExport('csv')"
            >
              导出 CSV 表格
            </el-button>

            <el-button
              type="primary"
              size="default"
              class="!bg-blue-600 hover:!bg-blue-700 font-medium"
              :loading="exportingExcel"
              :disabled="busy || loadingStudy || !activeTaskId || selectedFamilies.length === 0"
              @click="handleExport('excel')"
            >
              <el-icon class="mr-1.5"><Download /></el-icon>
              导出 Excel (.xlsx)
            </el-button>
          </div></footer>
  </div></ClinicalShell>
</template>

<script setup lang="ts">
import RadiomicsProcessingFields from '@/components/RadiomicsProcessingFields.vue';

import RadiomicsFamilyCards from '@/components/RadiomicsFamilyCards.vue';

import RadiomicsPresetCards from '@/components/RadiomicsPresetCards.vue';

import { ref, reactive, computed, onMounted, watch } from 'vue';
import { useRoute } from 'vue-router';
import { ArrowLeft, Refresh, Download, DocumentDelete } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import axios from 'axios';
import api from '@/api/client';
import ClinicalShell from '@/components/ClinicalShell.vue';
import { sortedTasks, taskId } from '@/ui/clinical';
import { positiveId } from '@/ui/workspaceNavigation';


const route = useRoute();
const fromWorkspace = computed(() => route.query.from === 'workspace' && positiveId(route.query.study) !== null);
const backTarget = computed(() => fromWorkspace.value
  ? { name: 'MedicalWorkspace', params: { studyId: String(positiveId(route.query.study)) }, query: positiveId(route.query.task) ? { task: String(positiveId(route.query.task)) } : {}, state: { workspaceView: window.history.state?.workspaceView ?? null } }
  : { name: 'StudyDashboard' });

const studies = ref<any[]>([]);
const selectedStudyId = ref<number | null>(null);
const currentStudy = ref<any>(null);
const activeTaskId = ref<number | null>(null);
const activeLesions = ref<any[]>([]);

const presets = ref<any[]>([]);
const families = ref<any[]>([]);
const activePresetId = ref<string>('ibsi_full');
const selectedFamilies = ref<string[]>([]);
const loadingStudy=ref(false), studyError=ref('');
let studyGeneration=0;

const form = reactive({
  resampling_mode: 'isotropic_1mm',
  custom_spacing: [1.0, 1.0, 1.0],
  interpolator: 'sitkBSpline',
  bin_width: 25.0,
  resegmentation_enabled: false,
  resegment_min_hu: -100.0,
  resegment_max_hu: 150.0
});

const recalculating = ref(false);
const exportingExcel = ref(false);
const exportingCsv = ref(false);
const busy = computed(()=>recalculating.value||exportingExcel.value||exportingCsv.value);


function isFamilySelected(id: string) {
  return selectedFamilies.value.includes(id);
}

function toggleFamily(id: string) {
  if (selectedFamilies.value.includes(id)) {
    selectedFamilies.value = selectedFamilies.value.filter(item => item !== id);
  } else {
    selectedFamilies.value.push(id);
  }
  activePresetId.value = '';
}

function selectAllFamilies(all: boolean) {
  if (all) {
    selectedFamilies.value = families.value.map(f => f.id);
  } else {
    selectedFamilies.value = [];
  }
  activePresetId.value = '';
}

function applyPreset(preset: any) {
  activePresetId.value = preset.id;
  const cfg = preset.config;
  if (cfg.selected_classes) {
    selectedFamilies.value = [...cfg.selected_classes];
  }
  if (cfg.resampling_mode) form.resampling_mode = cfg.resampling_mode;
  if (cfg.custom_spacing) form.custom_spacing = [...cfg.custom_spacing];
  if (cfg.bin_width !== undefined) form.bin_width = cfg.bin_width;
  if (cfg.interpolator) form.interpolator = cfg.interpolator;
  if (cfg.resegmentation_enabled !== undefined) form.resegmentation_enabled = cfg.resegmentation_enabled;
}

const tablePreviewData = computed(() => {
  return activeLesions.value.map(l => {
    const clin = l.features?.clinical_summary || {};
    const morph = l.features?.morphology || {};
    const inten = l.features?.intensity || {};
    const text = l.features?.texture || {};
    return {
      lesion_id: l.lesion_index,
      density_type: l.density_type,
      volume_mm3: morph.volume_mm3 || l.volume_mm3,
      max_diameter_mm: morph.max_3d_diameter_mm || l.equivalent_diameter_mm,
      mean_hu: inten.mean_hu ?? 'N/A',
      sphericity: morph.sphericity ?? 'N/A',
      entropy: inten.entropy ?? 'N/A',
      glcm_contrast: text.glcm_contrast ?? 'N/A'
    };
  });
});

async function fetchStudies() {
  try {
    const res: any = await api.get('/studies');
    studies.value = res || [];
    const requested = positiveId(route.query.study);
    if (route.query.study !== undefined && !requested) {
      studyError.value = '检查编号无效，请返回后重新选择。';
      return;
    }
    if (requested) {
      // Fetch the exact study even if the list is paginated or excludes it.
      let target = studies.value.find(s => s.id === requested);
      if (!target) {
        target = await api.get('/studies/' + requested);
        studies.value.push(target);
      }
      selectedStudyId.value = requested;
      await onStudyChange(requested);
    } else if (studies.value.length > 0) {
      selectedStudyId.value = (studies.value.find(s=>sortedTasks(s).some(t=>t.status==='COMPLETED')) || studies.value[0]).id;
      await onStudyChange(selectedStudyId.value!);
    }
  } catch (e: any) {
    studyError.value='检查列表读取失败，请重试。';
  }
}

async function onStudyChange(studyId: number) {
  const generation=++studyGeneration;
  currentStudy.value = studies.value.find(s => s.id === studyId);
  activeTaskId.value=null;activeLesions.value=[];presets.value=[];families.value=[];selectedFamilies.value=[];studyError.value='';
  const requested = positiveId(route.query.task);
  const useRequestedTask = studyId === positiveId(route.query.study) && route.query.task !== undefined;
  const completed = sortedTasks(currentStudy.value).find(t => t.status === 'COMPLETED' && (!useRequestedTask || taskId(t) === requested));
  if (useRequestedTask && !completed) {
    loadingStudy.value=false;
    studyError.value='指定分析任务不存在或尚未完成，请返回影像检查后重试。';
    return;
  }
  const id=taskId(completed);
  loadingStudy.value=!!id;
  if(!id)return;
  try {
    const [results,catalog]:any[]=await Promise.all([api.get('/results/'+id),api.get('/results/'+id+'/radiomics-catalog')]);
    if(generation!==studyGeneration)return;
    activeLesions.value=Array.isArray(results?.lesions)?results.lesions:[];
    presets.value=catalog?.presets||[];families.value=catalog?.families||[];
    selectedFamilies.value=families.value.map((f:any)=>f.id);
    activePresetId.value='';
    activeTaskId.value=id;
  } catch {
    if(generation===studyGeneration)studyError.value='结果或特征目录读取失败，请重试。';
  } finally {
    if(generation===studyGeneration)loadingStudy.value=false;
  }
}

async function handleRecalculate() {
  if (!activeTaskId.value || busy.value || loadingStudy.value || !selectedFamilies.value.length) return;
  recalculating.value = true;
  try {
    const payload = {
      preset_id: activePresetId.value || undefined,
      bin_width: form.bin_width,
      resampling_mode: form.resampling_mode,
      custom_spacing: form.custom_spacing,
      interpolator: form.interpolator,
      resegmentation_enabled: form.resegmentation_enabled,
      resegment_min_hu: form.resegment_min_hu,
      resegment_max_hu: form.resegment_max_hu,
      selected_classes: selectedFamilies.value
    };

    const res: any = await api.post(`/results/${activeTaskId.value}/radiomics/recalculate`, payload);
    if (res?.lesions) {
      activeLesions.value = res.lesions;
    }
    ElMessage.success('特征重算完成！预览数据已实时更新');
  } catch (e: any) {
    ElMessage.error(e.message || '特征重新提取失败');
  } finally {
    recalculating.value = false;
  }
}

async function handleExport(format: 'excel' | 'csv') {
  if (!activeTaskId.value || busy.value || loadingStudy.value || !selectedFamilies.value.length) return;
  if (format === 'excel') exportingExcel.value = true;
  else exportingCsv.value = true;

  try {
    const payload = {
      format,
      preset_id: activePresetId.value || undefined,
      bin_width: form.bin_width,
      resampling_mode: form.resampling_mode,
      custom_spacing: form.custom_spacing,
      interpolator: form.interpolator,
      resegmentation_enabled: form.resegmentation_enabled,
      resegment_min_hu: form.resegment_min_hu,
      resegment_max_hu: form.resegment_max_hu,
      selected_classes: selectedFamilies.value
    };

    const token = localStorage.getItem('token');
    const resp = await axios.post(`/api/v1/results/${activeTaskId.value}/radiomics/export`, payload, {
      responseType: 'blob',
      headers: {
        Authorization: token ? `Bearer ${token}` : ''
      }
    });

    const blob = new Blob([resp.data], {
      type: format === 'excel'
        ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        : 'text/csv;charset=utf-8'
    });

    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const ext = format === 'excel' ? 'xlsx' : 'csv';
    link.download = `radiomics_study_${selectedStudyId.value}_task_${activeTaskId.value}.${ext}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);

    ElMessage.success(`组学数据已成功导出为 .${ext} 文件！`);
  } catch (e: any) {
    ElMessage.error(e.message || '导出文件失败');
  } finally {
    exportingExcel.value = false;
    exportingCsv.value = false;
  }
}

onMounted(() => {
  fetchStudies();
});
watch(() => [route.query.study, route.query.task], () => {
  void fetchStudies();
});
</script>

<style scoped>
:deep(.el-collapse-item__header) {
  font-size: 11px;
  color: #2563eb;
  height: 28px;
  line-height: 28px;
}
</style>
