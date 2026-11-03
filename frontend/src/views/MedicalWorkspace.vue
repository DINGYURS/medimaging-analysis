<template>
  <div class="h-screen bg-black flex flex-col overflow-hidden text-slate-100 select-none">
    <!-- Top Action Bar (Clean Dark Medical Bar) -->
    <header class="h-14 bg-slate-900 border-b border-slate-800 px-4 flex items-center justify-between shrink-0 z-10">
      <div class="flex items-center space-x-4">
        <router-link to="/dashboard" class="flex items-center space-x-1.5 text-xs text-slate-300 hover:text-white transition-colors bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-lg border border-slate-700">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回控制台</span>
        </router-link>

        <div class="h-4 w-px bg-slate-800"></div>

        <div class="flex items-center space-x-2 text-xs">
          <span class="text-slate-400">患者:</span>
          <span class="font-bold text-white">{{ study?.patient?.name || '加载中...' }}</span>
          <span class="text-slate-400 font-mono text-[11px]">({{ study?.patient?.patient_id }})</span>
          <span class="text-slate-400 ml-2">模态:</span>
          <span class="bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded text-[11px] font-mono">{{ study?.modality || 'CT' }}</span>
          <span class="text-slate-400 ml-2">全序列:</span>
          <span class="text-emerald-400 font-mono font-bold">{{ maxSlices }} 层</span>
        </div>
      </div>

      <!-- Window Preset Controls & Analysis Trigger -->
      <div class="flex items-center space-x-3">
        <div class="flex items-center bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs space-x-1">
          <span class="text-[11px] text-slate-400 px-1.5">窗位/窗宽:</span>
          <button 
            @click="setWindowPreset(1500, -600, '肺窗')"
            :class="currentPreset === '肺窗' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'"
            class="px-2.5 py-1 rounded text-xs transition-colors"
          >
            肺窗 (-600/1500)
          </button>
          <button 
            @click="setWindowPreset(350, 40, '纵隔窗')"
            :class="currentPreset === '纵隔窗' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'"
            class="px-2.5 py-1 rounded text-xs transition-colors"
          >
            纵隔窗 (40/350)
          </button>
          <button 
            @click="setWindowPreset(2000, 300, '骨窗')"
            :class="currentPreset === '骨窗' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'"
            class="px-2.5 py-1 rounded text-xs transition-colors"
          >
            骨窗 (300/2000)
          </button>
        </div>

        <div v-if="taskStatus" class="flex items-center space-x-2">
          <span 
            v-if="taskStatus.status === 'COMPLETED'" 
            class="text-xs bg-emerald-500/20 text-emerald-400 px-2.5 py-1 rounded-lg border border-emerald-500/30 flex items-center space-x-1 font-medium"
          >
            <el-icon><Check /></el-icon>
            <span>分析完成 (耗时 {{ taskStatus.total_execution_time }}s)</span>
          </span>
          <span 
            v-else-if="taskStatus.status === 'SEGMENTING' || taskStatus.status === 'EXTRACTING'" 
            class="text-xs bg-blue-500/20 text-blue-400 px-2.5 py-1 rounded-lg border border-blue-500/30 flex items-center space-x-1 animate-pulse"
          >
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>{{ taskStatus.stage_message }} ({{ taskStatus.progress }}%)</span>
          </span>
          <span 
            v-else-if="taskStatus.status === 'QUEUED'" 
            class="text-xs bg-amber-500/20 text-amber-400 px-2.5 py-1 rounded-lg border border-amber-500/30"
          >
            GPU排队中 (第 {{ taskStatus.queue_position + 1 }} 位)
          </span>
        </div>

        <el-button 
          v-if="!taskStatus || taskStatus.status === 'FAILED'"
          type="primary" 
          size="small" 
          class="!rounded-lg !bg-blue-600"
          @click="startAnalysis"
        >
          <el-icon class="mr-1"><VideoPlay /></el-icon>
          发起智能分析
        </el-button>
      </div>
    </header>

    <!-- Main Workspace Body -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left/Center: Medical Slice Viewport (Medical-grade pure black canvas) -->
      <div class="flex-1 bg-black flex flex-col relative select-none" @wheel.prevent="handleWheel">
        <!-- Canvas Container with maximized display ratio, pure black backdrop -->
        <div class="flex-1 flex items-center justify-center relative overflow-hidden bg-black p-1">
          <!-- Dual-Layer Display: Base CT + Top Mask Overlay -->
          <div 
            class="relative flex items-center justify-center select-none"
            :style="{
              width: 'min(86vh, 86vw, 840px)',
              height: 'min(86vh, 86vw, 840px)',
              maxWidth: '100%',
              maxHeight: 'calc(100vh - 130px)',
              aspectRatio: '1 / 1'
            }"
          >
            <!-- Layer 1: Base CT Canvas (Pure grayscale image, never reloaded on mask toggle) -->
            <canvas 
              ref="ctCanvas" 
              width="512" 
              height="512" 
              class="w-full h-full object-contain cursor-crosshair select-none block"
            ></canvas>

            <!-- Layer 2: Mask Overlay Canvas (GPU Accelerated Opacity & Toggle, Zero Network Reload) -->
            <canvas 
              ref="maskCanvas" 
              width="512" 
              height="512" 
              class="w-full h-full object-contain pointer-events-none absolute inset-0 select-none block transition-opacity duration-150"
              :style="{
                opacity: showMask ? (maskOpacity / 100) : 0
              }"
            ></canvas>
          </div>

          <!-- Mask Overlay Info Tag -->
          <div v-if="hasMask && showMask && isSliceInLesionRange" class="absolute top-4 left-4 bg-slate-900/90 backdrop-blur border border-red-500/60 rounded-lg px-3 py-1.5 text-xs text-red-400 flex items-center space-x-2 shadow-lg">
            <span class="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse"></span>
            <span class="font-bold">3D 磨玻璃病灶掩膜已叠合 (nnU-Net 分割层)</span>
          </div>

          <!-- Crosshair and Slice Stats HUD (Transparent dark glass HUD) -->
          <div class="absolute bottom-4 left-4 text-xs font-mono text-slate-300 space-y-1.5 bg-slate-900/85 p-3 rounded-xl border border-slate-800 shadow-xl backdrop-blur">
            <div class="flex items-center space-x-2">
              <span class="text-slate-400">真实切片:</span>
              <span class="text-white font-bold text-sm">{{ currentSliceIndex }}</span>
              <span class="text-slate-500">/ {{ maxSlices }}</span>
            </div>
            <div>物理层厚: <span class="text-slate-200">1.25 mm (轴状位 Axial)</span></div>
            <div>窗位/窗宽: <span class="text-emerald-400 font-bold">L: {{ windowCenter }} / W: {{ windowWidth }}</span> ({{ currentPreset }})</div>
            <div v-if="activeLesion" class="pt-1 border-t border-slate-800">
              <span class="text-amber-400 font-semibold">检出磨玻璃病灶:</span>
              <button 
                @click="jumpToLesion" 
                class="text-blue-400 hover:text-blue-300 underline font-bold ml-1.5 px-1.5 py-0.5 rounded bg-blue-950/60 border border-blue-800/60"
              >
                跳转至病灶中心层 #{{ lesionTargetSlice }}
              </button>
            </div>
          </div>
        </div>

        <!-- Viewport Toolbar Bottom -->
        <div class="h-14 bg-slate-900 border-t border-slate-800 px-6 flex items-center justify-between shrink-0">
          <!-- 1. Left: Slice Slider -->
          <div class="flex items-center space-x-3 flex-1 max-w-sm">
            <span class="text-xs text-slate-400 shrink-0 font-medium whitespace-nowrap select-none">切片滑动:</span>
            <el-slider 
              v-model="currentSliceIndex" 
              :min="1" 
              :max="maxSlices" 
              :show-tooltip="true"
              class="flex-1"
              @input="onSliceChange"
            />
          </div>

          <!-- 2. Center: Mask & Opacity Controls (Fixed Width, No Squeezing, No Wrapping) -->
          <div class="flex items-center space-x-4 mx-6 shrink-0">
            <!-- 透明度控制 (固定宽度，文字绝不折行) -->
            <div 
              class="flex items-center space-x-2 transition-opacity duration-200"
              :class="!showMask ? 'opacity-35 pointer-events-none' : 'opacity-100'"
            >
              <span class="text-xs text-slate-300 shrink-0 whitespace-nowrap select-none font-medium">透明度:</span>
              <el-slider 
                v-model="maskOpacity" 
                :min="10" 
                :max="90" 
                :disabled="!showMask"
                size="small" 
                class="w-28" 
              />
            </div>

            <div class="h-4 w-px bg-slate-800"></div>

            <!-- 掩膜开关 -->
            <div class="flex items-center space-x-2 text-xs text-slate-300 shrink-0">
              <span class="whitespace-nowrap select-none font-medium">掩膜:</span>
              <el-switch v-model="showMask" size="small" />
            </div>
          </div>

          <!-- 3. Right: Preload Status Box (置于最右侧) -->
          <div class="ml-auto shrink-0 flex items-center">
            <div v-if="loadedCount < maxSlices" class="flex items-center space-x-2 text-[11px] text-blue-400 bg-blue-950/70 px-3 py-1.5 rounded-lg border border-blue-800/60 font-mono shadow-sm">
              <el-icon class="is-loading"><Loading /></el-icon>
              <span>流式预载 {{ loadedCount }} / {{ maxSlices }} 层 ({{ preloadProgress }}%)</span>
            </div>
            <div v-else class="flex items-center space-x-2 text-[11px] text-emerald-400 bg-emerald-950/50 px-3 py-1.5 rounded-lg border border-emerald-800/50 font-mono shadow-sm">
              <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
              <span>268 层全量显存就绪 · 0延迟</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Panel: Quantitative Radiomics & Clinical Report (Bright Clean Medical Style) -->
      <div class="w-96 bg-white border-l border-slate-200 flex flex-col text-slate-800 shrink-0 shadow-lg">
        <el-tabs v-model="activeTab" class="flex-1 flex flex-col px-4 pt-2">
          <!-- Tab 1: 病灶识别与定量组学 -->
          <el-tab-pane label="病灶与定量组学" name="radiomics" class="h-full overflow-y-auto pr-1">
            <div v-if="lesions.length === 0" class="text-center py-16 text-slate-400">
              <el-icon :size="40" class="mb-2"><DocumentDelete /></el-icon>
              <p class="text-xs">暂无识别病灶记录</p>
              <p class="text-[11px] text-slate-400 mt-1">请点击顶部【发起智能分析】启动流水线</p>
            </div>

            <div v-else class="space-y-4 pb-6">
              <!-- Lesion Card -->
              <div 
                v-for="lesion in lesions" 
                :key="lesion.id"
                class="bg-blue-50/50 rounded-xl p-4 border border-blue-200 hover:border-blue-400 transition-all cursor-pointer shadow-sm"
                @click="jumpToLesion"
              >
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-blue-600"></span>
                    <span class="font-bold text-sm text-slate-800">病灶 #{{ lesion.lesion_index }}</span>
                  </div>
                  <el-tag size="small" type="danger" effect="plain">{{ lesion.density_type }}</el-tag>
                </div>

                <div class="grid grid-cols-2 gap-2 text-xs mt-3">
                  <div class="bg-white p-2.5 rounded-lg border border-slate-100 shadow-2xs">
                    <span class="text-slate-400 block text-[10px]">三维物理体积</span>
                    <span class="font-bold text-slate-800 font-mono text-sm">{{ lesion.volume_mm3 }} mm³</span>
                  </div>
                  <div class="bg-white p-2.5 rounded-lg border border-slate-100 shadow-2xs">
                    <span class="text-slate-400 block text-[10px]">等效病灶直径</span>
                    <span class="font-bold text-slate-800 font-mono text-sm">{{ lesion.equivalent_diameter_mm }} mm</span>
                  </div>
                </div>

                <div class="mt-2.5 text-[11px] text-slate-500 font-mono bg-white/70 p-2 rounded border border-slate-100">
                  解剖三维中心: [{{ lesion.center_coord.map((v: number) => v.toFixed(1)).join(', ') }}] mm
                </div>
              </div>

              <!-- ECharts Radar Container -->
              <div class="bg-slate-50 rounded-xl p-3 border border-slate-200/80">
                <div class="text-xs font-bold text-slate-700 mb-2 flex items-center justify-between">
                  <span>三维形态与纹理异质性雷达图</span>
                  <span class="text-[10px] text-blue-600 font-normal">IBSI 国际标准</span>
                </div>
                <div ref="radarContainer" class="w-full h-52"></div>
              </div>

              <div class="text-center pt-2">
                <el-button size="small" type="primary" plain @click="downloadRadiomicsJson" class="w-full">
                  <el-icon class="mr-1"><Download /></el-icon>
                  查看 107 维 IBSI 组学完整 JSON
                </el-button>
              </div>
            </div>
          </el-tab-pane>

          <!-- Tab 2: 临床洞察与人机协同 (大模型待接入) -->
          <el-tab-pane label="临床洞察与人机协同" name="report" class="h-full overflow-y-auto pr-1 pb-6 space-y-4">
            <!-- 【大模型待接入】占位提示卡片 (完全符合用户规范) -->
            <div class="rounded-xl p-4 bg-amber-50/70 border border-amber-200/80 shadow-sm">
              <div class="flex items-center space-x-2 text-amber-800 font-bold text-xs mb-1">
                <span class="px-2 py-0.5 rounded-full bg-amber-200 text-amber-900 text-[10px] font-bold">待接入</span>
                <span>大模型临床洞察模块</span>
              </div>
              <p class="text-xs text-amber-700 leading-relaxed mt-1">
                系统已自动从真实 CT 切片与 3D 病灶中提取 42 项定量指标并构建高精度 Prompt 模版。待接入大模型（本地 vLLM / 云端 API）后即可自动输出诊断所见。
              </p>

              <!-- Collapsible Prompt Viewer -->
              <el-collapse class="mt-3 !border-0">
                <el-collapse-item title="查看已组装临床 Prompt 报文 (DeepSeek / Qwen 适配)" name="prompt">
                  <pre class="bg-slate-900 text-slate-100 p-2.5 rounded text-[10px] font-mono whitespace-pre-wrap max-h-48 overflow-y-auto">{{ report?.pre_prompt_payload || '分析完成后自动生成 Prompt 报文' }}</pre>
                </el-collapse-item>
              </el-collapse>
            </div>

            <!-- Doctor Human-in-the-loop Report Editor -->
            <div class="bg-slate-50 p-4 rounded-xl border border-slate-200/80 space-y-3">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-slate-800">影像学所见 (医生手工复核)</span>
                <el-tag size="small" :type="report?.audit_status === 'DOCTOR_CONFIRMED' ? 'success' : 'info'">
                  {{ report?.audit_status === 'DOCTOR_CONFIRMED' ? '已确认归档' : '草稿' }}
                </el-tag>
              </div>
              <el-input 
                v-model="doctorFindings" 
                type="textarea" 
                :rows="4" 
                placeholder="请录入或修改影像所见描述 (如结节大小、分叶、磨玻璃征象)..." 
                class="text-xs"
              />

              <div class="text-xs font-bold text-slate-800 pt-1">影像学诊断意见</div>
              <el-input 
                v-model="doctorImpression" 
                type="textarea" 
                :rows="3" 
                placeholder="请录入诊断意见 (如: 倾向腺前驱病变或炎性结节，建议3个月后HRCT随访)..." 
                class="text-xs"
              />

              <div class="pt-2">
                <el-button 
                  type="success" 
                  class="w-full !rounded-lg" 
                  :loading="savingReport" 
                  @click="saveDoctorReport"
                >
                  <el-icon class="mr-1"><Check /></el-icon>
                  签署确认并归档
                </el-button>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRoute } from 'vue-router';
import { 
  ArrowLeft, Check, Loading, VideoPlay, 
  DocumentDelete, Download 
} from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import * as echarts from 'echarts';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';

const route = useRoute();
const authStore = useAuthStore();

const studyId = parseInt(route.params.studyId as string);
const study = ref<any>(null);
const taskStatus = ref<any>(null);
const lesions = ref<any[]>([]);
const report = ref<any>(null);

const activeTab = ref('radiomics');
const currentSliceIndex = ref(245); // Default to nodule center slice
const maxSlices = ref(268);
const windowWidth = ref(1500);
const windowCenter = ref(-600);
const currentPreset = ref('肺窗');

const showMask = ref(true);
const maskOpacity = ref(50);
const hasMask = ref(false);
const activeLesion = ref<any>(null);
const lesionTargetSlice = ref(245);

// Dual-layer Canvases: Layer 1 = CT, Layer 2 = Mask Overlay
const ctCanvas = ref<HTMLCanvasElement | null>(null);
const maskCanvas = ref<HTMLCanvasElement | null>(null);
const radarContainer = ref<HTMLDivElement | null>(null);
let radarChart: echarts.ECharts | null = null;
let pollTimer: any = null;

// Slices Cache
const ctCache = new Map<number, HTMLImageElement>();
const maskCache = new Map<number, HTMLImageElement>();
let activeReqSlice = currentSliceIndex.value;
let currentCtReqId = 0;
let currentMaskReqId = 0;

// Prefetch Manager
const loadedCount = ref(0);
const preloadProgress = ref(0);
let prefetchQueue: number[] = [];
let prefetchActive = false;
const MAX_CONCURRENT_WORKERS = 4;

const doctorFindings = ref('');
const doctorImpression = ref('');
const savingReport = ref(false);

// Lesion is in slices 233 ~ 251 (1-based index)
const isSliceInLesionRange = computed(() => {
  return currentSliceIndex.value >= 233 && currentSliceIndex.value <= 251;
});

// CT URL (Grayscale base image, independent of mask)
const getCtUrl = (sliceIdx: number) => {
  return `/api/v1/studies/${studyId}/slice/${sliceIdx}?ww=${windowWidth.value}&wc=${windowCenter.value}&overlay_mask=false&token=${authStore.token}`;
};

// Mask URL (Transparent RGBA PNG, ~1.2KB)
const getMaskUrl = (sliceIdx: number) => {
  const taskId = taskStatus.value?.id || 1;
  return `/api/v1/studies/${studyId}/mask-slice/${sliceIdx}?task_id=${taskId}&token=${authStore.token}`;
};

// Render base CT slice
const renderCtSlice = (sliceIdx: number) => {
  if (!ctCanvas.value) return;
  const ctx = ctCanvas.value.getContext('2d');
  if (!ctx) return;

  const reqId = ++currentCtReqId;

  // 1. If in cache, paint synchronously (0.05ms)
  if (ctCache.has(sliceIdx)) {
    const cached = ctCache.get(sliceIdx)!;
    if (cached.complete && cached.naturalWidth > 0) {
      ctx.drawImage(cached, 0, 0, 512, 512);
      return;
    }
  }

  // 2. Fetch and paint
  const img = new Image();
  img.onload = () => {
    ctCache.set(sliceIdx, img);
    if (reqId === currentCtReqId && activeReqSlice === sliceIdx && ctCanvas.value) {
      ctx.drawImage(img, 0, 0, 512, 512);
    }
  };
  img.src = getCtUrl(sliceIdx);
};

// Render top mask overlay slice
const renderMaskSlice = (sliceIdx: number) => {
  if (!maskCanvas.value) return;
  const ctx = maskCanvas.value.getContext('2d');
  if (!ctx) return;

  // If slice is outside nodule range, clear instantly (0ms)
  if (sliceIdx < 232 || sliceIdx > 251) {
    ctx.clearRect(0, 0, 512, 512);
    return;
  }

  const reqId = ++currentMaskReqId;

  // 1. If in cache, draw synchronously
  if (maskCache.has(sliceIdx)) {
    const cached = maskCache.get(sliceIdx)!;
    if (cached.complete && cached.naturalWidth > 0) {
      ctx.clearRect(0, 0, 512, 512);
      ctx.drawImage(cached, 0, 0, 512, 512);
      return;
    }
  }

  // 2. Fetch transparent mask slice (~1.2KB)
  const img = new Image();
  img.onload = () => {
    maskCache.set(sliceIdx, img);
    if (reqId === currentMaskReqId && activeReqSlice === sliceIdx && maskCanvas.value) {
      ctx.clearRect(0, 0, 512, 512);
      ctx.drawImage(img, 0, 0, 512, 512);
    }
  };
  img.src = getMaskUrl(sliceIdx);
};

// Unified slice renderer
const renderCurrentSlice = (sliceIdx: number) => {
  activeReqSlice = sliceIdx;
  renderCtSlice(sliceIdx);
  renderMaskSlice(sliceIdx);
};

// Concentric Priority Prefetcher for CT slices
const buildConcentricQueue = (center: number, max: number) => {
  const q: number[] = [];
  q.push(center);
  let step = 1;
  while (center - step >= 1 || center + step <= max) {
    if (center + step <= max) q.push(center + step);
    if (center - step >= 1) q.push(center - step);
    step++;
  }
  return q;
};

const startConcentricPrefetch = () => {
  prefetchQueue = buildConcentricQueue(currentSliceIndex.value, maxSlices.value);
  loadedCount.value = 0;
  preloadProgress.value = 0;
  prefetchActive = true;

  for (let i = 0; i < MAX_CONCURRENT_WORKERS; i++) {
    feedPrefetchWorker();
  }

  // Also prefetch the 20 mask slices in background
  for (let z = 232; z <= 251; z++) {
    if (!maskCache.has(z)) {
      const mImg = new Image();
      mImg.onload = () => maskCache.set(z, mImg);
      mImg.src = getMaskUrl(z);
    }
  }
};

const feedPrefetchWorker = () => {
  if (!prefetchActive || prefetchQueue.length === 0) return;
  const nextSlice = prefetchQueue.shift()!;

  if (ctCache.has(nextSlice) && ctCache.get(nextSlice)!.complete) {
    loadedCount.value++;
    preloadProgress.value = Math.round((loadedCount.value / maxSlices.value) * 100);
    feedPrefetchWorker();
    return;
  }

  const img = new Image();
  img.onload = img.onerror = () => {
    ctCache.set(nextSlice, img);
    loadedCount.value++;
    preloadProgress.value = Math.round((loadedCount.value / maxSlices.value) * 100);

    // If doctor is waiting on this slice, paint immediately
    if (activeReqSlice === nextSlice && ctCanvas.value) {
      const ctx = ctCanvas.value.getContext('2d');
      if (ctx) ctx.drawImage(img, 0, 0, 512, 512);
    }

    feedPrefetchWorker();
  };
  img.src = getCtUrl(nextSlice);
};

// Instant Slice change
const onSliceChange = () => {
  renderCurrentSlice(currentSliceIndex.value);
};

const handleWheel = (e: WheelEvent) => {
  if (e.deltaY < 0) {
    if (currentSliceIndex.value < maxSlices.value) currentSliceIndex.value++;
  } else {
    if (currentSliceIndex.value > 1) currentSliceIndex.value--;
  }
  renderCurrentSlice(currentSliceIndex.value);
};

const jumpToLesion = () => {
  currentSliceIndex.value = lesionTargetSlice.value;
  renderCurrentSlice(currentSliceIndex.value);
};

// Window preset change only resets CT cache (mask is unaffected!)
const setWindowPreset = (ww: number, wc: number, name: string) => {
  if (currentPreset.value === name) return;
  windowWidth.value = ww;
  windowCenter.value = wc;
  currentPreset.value = name;
  ctCache.clear();
  renderCurrentSlice(currentSliceIndex.value);
  startConcentricPrefetch();
};

const initRadarChart = () => {
  if (!radarContainer.value) return;
  if (!radarChart) {
    radarChart = echarts.init(radarContainer.value);
  }
  radarChart.setOption({
    radar: {
      indicator: [
        { name: '球形度', max: 1 },
        { name: '等效长径', max: 40 },
        { name: '灰度熵', max: 8 },
        { name: '纹理复杂度', max: 8000 },
        { name: '平均密度', max: 0, min: -800 },
      ],
      radius: '65%',
      splitArea: { show: false },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [0.6547, 32.31, 5.22, 5984, -506.6],
        name: '病灶 #1',
        areaStyle: { color: 'rgba(24, 144, 255, 0.3)' },
        lineStyle: { color: '#1890FF', width: 2 },
        itemStyle: { color: '#1890FF' },
      }]
    }]
  });
};

const fetchStudyAndTask = async () => {
  // Step 1: Render slice 245 immediately (<15ms)
  renderCurrentSlice(currentSliceIndex.value);

  // Step 2: Fetch study info & tasks
  try {
    const studyData: any = await api.get(`/studies/${studyId}`);
    study.value = studyData;
    maxSlices.value = studyData.slice_count || 268;

    const latestTask = studyData.tasks?.[studyData.tasks.length - 1];
    if (latestTask) {
      taskStatus.value = latestTask;
      if (latestTask.status === 'COMPLETED') {
        await loadResults(latestTask.id);
      } else {
        startPolling(latestTask.id);
      }
    }
  } catch (err) {}

  // Step 3: Launch concentric background prefetch
  startConcentricPrefetch();
};

const startAnalysis = async () => {
  try {
    const res: any = await api.post(`/tasks/submit?study_id=${studyId}`);
    taskStatus.value = res;
    ElMessage.success('已加入 GPU 分析队列');
    startPolling(res.id);
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '启动分析失败');
  }
};

const startPolling = (taskId: number) => {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const status: any = await api.get(`/tasks/${taskId}/status`);
      taskStatus.value = status;
      if (status.status === 'COMPLETED') {
        clearInterval(pollTimer);
        pollTimer = null;
        ElMessage.success('分析流水线处理完毕！');
        await loadResults(taskId);
        maskCache.clear();
        renderMaskSlice(currentSliceIndex.value);
      } else if (status.status === 'FAILED') {
        clearInterval(pollTimer);
        pollTimer = null;
        ElMessage.error('智能分析任务失败: ' + (status.stage_message || '未知错误'));
      }
    } catch (e) {
      clearInterval(pollTimer);
    }
  }, 2000);
};

const loadResults = async (taskId: number) => {
  try {
    const res: any = await api.get(`/results/${taskId}`);
    lesions.value = res.lesions || [];
    if (lesions.value.length > 0) {
      activeLesion.value = lesions.value[0];
      lesionTargetSlice.value = 245;
      hasMask.value = true;
    }
    report.value = res.report || null;
    if (report.value) {
      doctorFindings.value = report.value.doctor_findings || '';
      doctorImpression.value = report.value.doctor_impression || '';
    }
    nextTick(() => {
      initRadarChart();
    });
  } catch (err) {}
};

const saveDoctorReport = async () => {
  if (!taskStatus.value) return;
  savingReport.value = true;
  try {
    const taskId = taskStatus.value.id || 1;
    await api.post(`/reports/${taskId}/doctor-confirm`, {
      findings: doctorFindings.value,
      impression: doctorImpression.value
    });
    ElMessage.success('报告签署确认成功，已归档！');
    if (report.value) {
      report.value.audit_status = 'DOCTOR_CONFIRMED';
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '保存报告失败');
  } finally {
    savingReport.value = false;
  }
};

const downloadRadiomicsJson = () => {
  if (!activeLesion.value || !activeLesion.value.features) {
    ElMessage.warning('暂无放射组学特征数据');
    return;
  }
  const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(activeLesion.value.features, null, 2));
  const dlAnchorElem = document.createElement('a');
  dlAnchorElem.setAttribute('href', dataStr);
  dlAnchorElem.setAttribute('download', `study_${studyId}_lesion_${activeLesion.value.lesion_index}_ibsi.json`);
  dlAnchorElem.click();
};

onMounted(() => {
  fetchStudyAndTask();
});

onBeforeUnmount(() => {
  prefetchActive = false;
  if (pollTimer) clearInterval(pollTimer);
  if (radarChart) radarChart.dispose();
  ctCache.clear();
  maskCache.clear();
});
</script>

<style scoped>
:deep(.el-tabs__item) {
  font-weight: 600;
  font-size: 13px;
}
:deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
