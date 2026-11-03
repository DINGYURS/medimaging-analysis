<template>
  <div class="min-h-screen bg-slate-50 flex flex-col">
    <!-- Top Navbar -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-30">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center font-bold text-lg shadow-sm">
            <el-icon><DataAnalysis /></el-icon>
          </div>
          <div>
            <h1 class="text-base font-bold text-slate-800 leading-tight">医学影像智能分析平台</h1>
            <p class="text-xs text-slate-500">单医生闭环工作台 &bull; 肺部CT病灶识别与定量组学</p>
          </div>
        </div>

        <div class="flex items-center space-x-4">
          <router-link 
            v-if="authStore.isAdmin"
            to="/admin/users"
            class="text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-lg transition-colors border border-blue-200"
          >
            ⚙️ 医生账号管理 (管理员)
          </router-link>

          <div class="flex items-center space-x-2 border-l border-slate-200 pl-4">
            <div class="text-right">
              <div class="text-xs font-semibold text-slate-700">{{ authStore.user?.real_name }}</div>
              <div class="text-[10px] text-slate-400">{{ authStore.user?.department }} ({{ authStore.user?.role }})</div>
            </div>
            <el-button size="small" type="danger" plain @click="authStore.logout">退出</el-button>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-1 w-full">
      <!-- Status Stats Cards -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div class="bg-white rounded-xl p-5 border border-slate-200/80 shadow-sm flex items-center justify-between">
          <div>
            <p class="text-xs font-medium text-slate-500">负责检查总数 (策略A独立)</p>
            <p class="text-2xl font-bold text-slate-800 mt-1">{{ studies.length }}</p>
          </div>
          <div class="w-12 h-12 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center text-xl">
            <el-icon><Files /></el-icon>
          </div>
        </div>

        <div class="bg-white rounded-xl p-5 border border-slate-200/80 shadow-sm flex items-center justify-between">
          <div>
            <p class="text-xs font-medium text-slate-500">GPU 并发推理限流门控</p>
            <p class="text-2xl font-bold text-emerald-600 mt-1">最大 2 并发</p>
          </div>
          <div class="w-12 h-12 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center text-xl">
            <el-icon><Cpu /></el-icon>
          </div>
        </div>

        <div class="bg-white rounded-xl p-5 border border-slate-200/80 shadow-sm flex items-center justify-between">
          <div>
            <p class="text-xs font-medium text-slate-500">大模型临床洞察模块</p>
            <p class="text-sm font-semibold text-amber-600 mt-2">预留接口（待接入）</p>
          </div>
          <div class="w-12 h-12 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center text-xl">
            <el-icon><ChatLineSquare /></el-icon>
          </div>
        </div>
      </div>

      <!-- Action & Table Bar -->
      <div class="bg-white rounded-xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-3">
            <h2 class="font-bold text-slate-800 text-base">患者影像与检查档案</h2>
            <span class="text-xs bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full font-medium">共 {{ studies.length }} 条记录</span>
          </div>

          <div class="flex items-center space-x-3">
            <el-button 
              type="primary" 
              class="!rounded-lg !bg-blue-600 hover:!bg-blue-700" 
              @click="showUploadDialog = true"
            >
              <el-icon class="mr-1"><UploadFilled /></el-icon>
              上传 DICOM 序列 (Zip)
            </el-button>
            <el-button @click="fetchStudies" :loading="loading">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </div>

        <!-- Studies Table -->
        <el-table :data="studies" v-loading="loading" stripe style="width: 100%">
          <el-table-column prop="patient.patient_id" label="病案号 / 患者 ID" min-width="150">
            <template #default="{ row }">
              <span class="font-mono font-medium text-blue-600">{{ row.patient?.patient_id }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="patient.name" label="患者姓名" min-width="120">
            <template #default="{ row }">
              <span class="font-medium text-slate-800">{{ row.patient?.name }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="modality" label="检查模态" width="100">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.modality }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="slice_count" label="切片层数" width="110">
            <template #default="{ row }">
              <span class="text-slate-600">{{ row.slice_count }} 张</span>
            </template>
          </el-table-column>

          <el-table-column prop="study_description" label="检查部位" min-width="160" show-overflow-tooltip />

          <el-table-column prop="created_at" label="导入时间" width="170">
            <template #default="{ row }">
              <span class="text-xs text-slate-400">{{ formatDate(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <div class="flex items-center space-x-2">
                <el-button 
                  size="small" 
                  type="primary" 
                  plain 
                  @click="goToWorkspace(row.id)"
                >
                  <el-icon class="mr-1"><View /></el-icon>
                  三维工作台
                </el-button>
                <el-button 
                  size="small" 
                  type="success" 
                  @click="triggerAnalyze(row.id)"
                >
                  智能分析
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </main>

    <!-- Upload Dialog -->
    <el-dialog 
      v-model="showUploadDialog" 
      title="上传 DICOM 序列压缩包" 
      width="540px"
      destroy-on-close
    >
      <div class="space-y-4">
        <el-upload
          drag
          action="#"
          :http-request="customUpload"
          :auto-upload="true"
          :show-file-list="false"
          accept=".zip,.dcm"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            将 DICOM 压缩包 (.zip) 拖拽到此处，或 <em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip text-xs text-slate-400">
              支持上传单个病例的 DICOM 序列 Zip 压缩包，系统将自动解压并转换为标准 3D NIfTI 体数据。
            </div>
          </template>
        </el-upload>

        <div v-if="uploading" class="space-y-2">
          <div class="text-xs text-blue-600 font-medium">正在上传与几何对齐转换...</div>
          <el-progress :percentage="uploadPercent" :status="uploadPercent === 100 ? 'success' : ''" />
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { 
  DataAnalysis, Files, Cpu, ChatLineSquare, 
  UploadFilled, Refresh, View 
} from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const authStore = useAuthStore();

const studies = ref<any[]>([]);
const loading = ref(false);
const showUploadDialog = ref(false);
const uploading = ref(false);
const uploadPercent = ref(0);

const fetchStudies = async () => {
  loading.value = true;
  try {
    const data: any = await api.get('/studies');
    studies.value = data;
  } catch (err) {
    // Handled
  } finally {
    loading.value = false;
  }
};

const goToWorkspace = (studyId: number) => {
  router.push(`/workspace/${studyId}`);
};

const triggerAnalyze = async (studyId: number) => {
  try {
    const res: any = await api.post('/tasks/analyze', { study_id: studyId });
    ElMessage.success(`智能分析任务已提交！流水号: ${res.task_code}`);
    router.push(`/workspace/${studyId}`);
  } catch (err) {
    // Handled
  }
};

const customUpload = async (options: any) => {
  const { file } = options;
  uploading.value = true;
  uploadPercent.value = 10;

  const formData = new FormData();
  formData.append('file', file);

  try {
    uploadPercent.value = 50;
    const res: any = await api.post('/studies/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total) {
          uploadPercent.value = Math.round((progressEvent.loaded * 90) / progressEvent.total);
        }
      }
    });
    uploadPercent.value = 100;
    ElMessage.success('DICOM 检查上传并几何解析成功！');
    showUploadDialog.value = false;
    await fetchStudies();
  } catch (err) {
    // Handled
  } finally {
    uploading.value = false;
    uploadPercent.value = 0;
  }
};

const formatDate = (dateStr: string) => {
  if (!dateStr) return '--';
  return new Date(dateStr).toLocaleString('zh-CN', { hour12: false });
};

onMounted(() => {
  fetchStudies();
});
</script>
