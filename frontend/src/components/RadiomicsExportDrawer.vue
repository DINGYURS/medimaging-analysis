<template>
  <el-drawer
    v-model="visible"
    title="特征计算与数据导出"
    direction="rtl"
    size="min(680px, 100vw)"
    class="radiomics-export-drawer c-radiomics"
    :before-close="handleClose" :close-on-press-escape="!busy" :close-on-click-modal="!busy" :show-close="!busy"
  >
    <div class="h-full flex flex-col justify-between -mt-4 text-slate-800">
      <!-- Scrollable Main Form -->
      <div class="flex-1 overflow-y-auto pr-2 space-y-5">
        <p v-if="loadingCatalog" class="c-muted" role="status">正在读取特征目录…</p><p v-if="catalogError" class="c-alert" role="alert">{{ catalogError }} <button class="c-link" @click="loadCatalog">重试</button></p>
        <!-- Section 1: Presets Selection -->
        <section class="c-radiomics-section">
          <div class="c-radiomics-section-head">
            <div><span class="c-step-label">STEP 01</span><h2>预设计算方案</h2></div>

          </div>

          <RadiomicsPresetCards :presets="presets" :active-preset-id="activePresetId" :busy="busy" @apply="applyPreset" />
        </section>

        <!-- Section 2: Feature Families Selection -->
        <section class="c-radiomics-section">
          <div class="c-radiomics-section-head">
            <div><span class="c-step-label">STEP 02</span><h2>目标特征家族 ({{ selectedFamilies.length }} / {{ families.length }} 类已选)</h2></div>
            <div class="flex items-center space-x-2 text-[11px]">
              <button type="button" class="text-blue-600 hover:text-blue-700 font-medium" @click="selectAllFamilies(true)">全选</button>
              <span class="text-slate-300">|</span>
              <button type="button" class="text-slate-500 hover:text-slate-700" @click="selectAllFamilies(false)">清空</button>
            </div>
          </div>

          <!-- Feature Families Cards -->
          <RadiomicsFamilyCards :families="families" :selected-families="selectedFamilies" @toggle="toggleFamily" />
        </section>

        <!-- Section 3: IBSI Processing Protocol -->
        <section class="c-radiomics-section">
          <div class="c-radiomics-section-head">
            <div><span class="c-step-label">STEP 03</span><h2>参数设置</h2></div>
          </div>

          <RadiomicsProcessingFields :form="form" />
        </section>
      </div>

      <!-- Bottom Sticky Action Footer -->
      <div class="c-drawer-export-footer">
        <div class="text-xs text-slate-500">
          <span>共勾选 </span>
          <strong class="text-slate-800 font-mono text-sm">{{ selectedFamilies.length }}</strong>
          <span> / {{ families.length }} 个家族</span>
        </div>

        <div class="c-export-actions">
          <!-- Recalculate Button -->
          <el-button
            size="default"
            :loading="recalculating"
            :disabled="busy || selectedFamilies.length === 0"
            @click="handleRecalculate"
          >
            <el-icon class="mr-1"><Refresh /></el-icon>
            按新参数重算
          </el-button>

          <!-- Export CSV Button -->
          <el-button
            size="default"
            :loading="exportingCsv"
            :disabled="busy || selectedFamilies.length === 0"
            @click="handleExport('csv')"
          >
            导出 CSV
          </el-button>

          <!-- Export Excel Button -->
          <el-button
            type="primary"
            size="default"
            class="!bg-blue-600 hover:!bg-blue-700"
            :loading="exportingExcel"
            :disabled="busy || selectedFamilies.length === 0"
            @click="handleExport('excel')"
          >
            <el-icon class="mr-1"><Download /></el-icon>
            导出 Excel (.xlsx)
          </el-button>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import RadiomicsProcessingFields from '@/components/RadiomicsProcessingFields.vue';

import RadiomicsFamilyCards from '@/components/RadiomicsFamilyCards.vue';

import RadiomicsPresetCards from '@/components/RadiomicsPresetCards.vue';

import { ref, reactive, computed, watch } from 'vue';
import { Refresh, Download } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import axios from 'axios';
import api from '@/api/client';

const props = defineProps<{
  modelValue: boolean;
  taskId: number | null;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
  'recalculated': [newResults: any];
}>();

const visible = computed({
  get: () => props.modelValue,
  set: (val: boolean) => emit('update:modelValue', val)
});

const presets = ref<any[]>([]);
const families = ref<any[]>([]);
const activePresetId = ref<string>('clinical_default');
const selectedFamilies = ref<string[]>([]);
const catalogError=ref(''),loadingCatalog=ref(false);
let catalogGeneration=0;

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

async function loadCatalog() {
  const generation=++catalogGeneration,id=props.taskId;
  selectedFamilies.value=[];families.value=[];presets.value=[];catalogError.value='';
  loadingCatalog.value=!!id;
  if(!id)return;
  try {
    const res:any=await api.get('/results/'+id+'/radiomics-catalog');
    if(generation!==catalogGeneration)return;
    presets.value=res?.presets||[];families.value=res?.families||[];
    const preset=presets.value.find(p=>p.id==='clinical_default');
    if(preset)applyPreset(preset);
    else {selectedFamilies.value=families.value.map(f=>f.id);activePresetId.value='';}
    selectedFamilies.value=selectedFamilies.value.filter(id=>families.value.some(f=>f.id===id));
  } catch {
    if(generation===catalogGeneration)catalogError.value='特征目录读取失败，请重试。';
  } finally {
    if(generation===catalogGeneration)loadingCatalog.value=false;
  }
}

watch(() => props.taskId, () => { void loadCatalog(); }, { immediate: true });

function handleClose() {
  if(busy.value)return;
  visible.value = false;
}

async function handleRecalculate() {
  if (!props.taskId || busy.value || !selectedFamilies.value.length) return;
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

    const res: any = await api.post(`/results/${props.taskId}/radiomics/recalculate`, payload);
    ElMessage.success('特征重算完成！工作台量化指标已同步更新');
    emit('recalculated', res);
    visible.value = false;
  } catch (e: any) {
    ElMessage.error(e.message || '特征重新提取失败');
  } finally {
    recalculating.value = false;
  }
}

async function handleExport(format: 'excel' | 'csv') {
  if (!props.taskId || busy.value || !selectedFamilies.value.length) return;
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
    const resp = await axios.post(`/api/v1/results/${props.taskId}/radiomics/export`, payload, {
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
    link.download = `radiomics_task_${props.taskId}_${Date.now()}.${ext}`;
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


</script>

<style scoped>
.radiomics-export-drawer :deep(.el-drawer__header) {
  margin-bottom: 8px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
}
.radiomics-export-drawer :deep(.el-collapse-item__header) {
  font-size: 11px;
  color: #2563eb;
  height: 28px;
  line-height: 28px;
}
</style>
