<template>
  <div class="min-h-screen bg-slate-50 flex flex-col justify-center items-center px-4">
    <div class="max-w-md w-full bg-white rounded-2xl shadow-xl border border-slate-200/80 p-8">
      <!-- Header -->
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-blue-50 text-blue-600 mb-4 shadow-sm border border-blue-100">
          <el-icon :size="32"><Monitor /></el-icon>
        </div>
        <h2 class="text-2xl font-bold text-slate-800 tracking-tight">医学影像智能分析平台</h2>
        <p class="text-sm text-slate-500 mt-2">单医生轻量级辅助诊断与影像组学系统</p>
      </div>

      <!-- Form -->
      <el-form :model="form" @submit.prevent="handleLogin" label-position="top">
        <el-form-item label="登录账号">
          <el-input 
            v-model="form.username" 
            placeholder="请输入账号 (如: doctor)" 
            size="large"
            :prefix-icon="User"
            clearable
          />
        </el-form-item>

        <el-form-item label="登录密码">
          <el-input 
            v-model="form.password" 
            type="password" 
            placeholder="请输入密码" 
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <div class="mt-6">
          <el-button 
            type="primary" 
            size="large" 
            class="w-full !rounded-xl !bg-blue-600 hover:!bg-blue-700 !font-medium"
            :loading="loading"
            native-type="submit"
          >
            立即登录进入工作台
          </el-button>
        </div>
      </el-form>

      <!-- Quick Demo Accounts -->
      <div class="mt-8 pt-6 border-t border-slate-100">
        <p class="text-xs text-slate-400 text-center mb-3 font-medium">快速体验账号填充</p>
        <div class="grid grid-cols-2 gap-3">
          <button 
            @click="fillAccount('doctor', 'doctor123')"
            class="py-2 px-3 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-blue-50 hover:text-blue-600 rounded-lg transition-colors border border-slate-200"
          >
            👨‍⚕️ 医生 (doctor)
          </button>
          <button 
            @click="fillAccount('admin', 'admin123')"
            class="py-2 px-3 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-blue-50 hover:text-blue-600 rounded-lg transition-colors border border-slate-200"
          >
            🛡️ 管理员 (admin)
          </button>
        </div>
      </div>
    </div>
    
    <div class="mt-6 text-center text-xs text-slate-400">
      &copy; 2026 医学影像智能分析平台 (MedImaging Analysis) &bull; A100 GPU 加速内核
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { User, Lock, Monitor } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const authStore = useAuthStore();

const form = reactive({
  username: 'doctor',
  password: 'doctor123',
});

const loading = ref(false);

const fillAccount = (u: string, p: string) => {
  form.username = u;
  form.password = p;
};

const handleLogin = async () => {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码');
    return;
  }
  loading.value = true;
  try {
    const user = await authStore.login(form.username, form.password);
    ElMessage.success(`欢迎回来，${user.real_name}`);
    if (user.role === 'ADMIN') {
      router.push('/dashboard');
    } else {
      router.push('/dashboard');
    }
  } catch (err) {
    // Handled in axios interceptor
  } finally {
    loading.value = false;
  }
};
</script>
