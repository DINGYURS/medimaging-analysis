<template>
  <div class="min-h-screen bg-slate-50 flex flex-col">
    <!-- Top Navbar -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-30">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <router-link to="/dashboard" class="flex items-center space-x-2 text-slate-500 hover:text-blue-600">
            <el-icon><ArrowLeft /></el-icon>
            <span class="text-sm">返回工作台</span>
          </router-link>
          <span class="text-slate-300">/</span>
          <h1 class="text-base font-bold text-slate-800">系统管理员 &bull; 医生账号权限管理</h1>
        </div>

        <div class="flex items-center space-x-3">
          <el-button type="primary" class="!rounded-lg" @click="showCreateDialog = true">
            <el-icon class="mr-1"><Plus /></el-icon>
            开通新医生账号
          </el-button>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-1 w-full">
      <div class="bg-white rounded-xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 flex items-center justify-between">
          <div>
            <h2 class="font-bold text-slate-800 text-base">系统注册医生与管理员列表</h2>
            <p class="text-xs text-slate-400 mt-1">每个医生享有独立的数据隔离空间 (策略 A)，管理员可进行全局权限分配</p>
          </div>
          <el-button @click="fetchUsers" :loading="loading"><el-icon><Refresh /></el-icon></el-button>
        </div>

        <el-table :data="users" v-loading="loading" stripe style="width: 100%">
          <el-table-column prop="id" label="UID" width="80" />
          <el-table-column prop="username" label="登录账号" min-width="120">
            <template #default="{ row }">
              <span class="font-mono font-medium text-slate-800">{{ row.username }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="real_name" label="姓名" min-width="120" />
          <el-table-column prop="department" label="所属科室" min-width="140" />
          <el-table-column prop="role" label="系统角色" width="120">
            <template #default="{ row }">
              <el-tag :type="row.role === 'ADMIN' ? 'danger' : 'primary'" size="small">
                {{ row.role === 'ADMIN' ? '超级管理员' : '临床医生' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="is_active" label="账号状态" width="120">
            <template #default="{ row }">
              <el-switch 
                v-model="row.is_active" 
                :disabled="row.id === authStore.user?.id"
                @change="toggleStatus(row)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="last_login_at" label="最后登录" width="180">
            <template #default="{ row }">
              <span class="text-xs text-slate-400">{{ formatDate(row.last_login_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="warning" plain @click="openResetPwd(row)">重置密码</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </main>

    <!-- Create Doctor Dialog -->
    <el-dialog v-model="showCreateDialog" title="开通新医生账号" width="460px">
      <el-form :model="createForm" label-position="top">
        <el-form-item label="登录账号 (Username)" required>
          <el-input v-model="createForm.username" placeholder="如: dr_wang" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="createForm.password" type="password" placeholder="请输入初始密码" show-password />
        </el-form-item>
        <el-form-item label="真实姓名" required>
          <el-input v-model="createForm.real_name" placeholder="如: 王医生" />
        </el-form-item>
        <el-form-item label="所属科室">
          <el-input v-model="createForm.department" placeholder="如: 胸外科 / 呼吸科" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreateUser">确认创建</el-button>
      </template>
    </el-dialog>

    <!-- Reset Password Dialog -->
    <el-dialog v-model="showResetDialog" :title="`重置密码 - ${selectedUser?.real_name} (${selectedUser?.username})`" width="420px">
      <el-form label-position="top">
        <el-form-item label="新密码" required>
          <el-input v-model="newPassword" type="password" placeholder="请输入新的密码" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showResetDialog = false">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="submitResetPassword">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue';
import { ArrowLeft, Plus, Refresh } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';

const authStore = useAuthStore();
const users = ref<any[]>([]);
const loading = ref(false);

const showCreateDialog = ref(false);
const creating = ref(false);
const createForm = reactive({
  username: '',
  password: '',
  real_name: '',
  department: '胸部放射科',
});

const showResetDialog = ref(false);
const resetting = ref(false);
const selectedUser = ref<any>(null);
const newPassword = ref('');

const fetchUsers = async () => {
  loading.value = true;
  try {
    const data: any = await api.get('/admin/users');
    users.value = data;
  } catch (err) {
    // Handled
  } finally {
    loading.value = false;
  }
};

const toggleStatus = async (user: any) => {
  try {
    await api.put(`/admin/users/${user.id}/status?is_active=${user.is_active}`);
    ElMessage.success(`用户 ${user.username} 状态已更新为: ${user.is_active ? '启用' : '禁用'}`);
  } catch (err) {
    user.is_active = !user.is_active; // Revert
  }
};

const submitCreateUser = async () => {
  if (!createForm.username || !createForm.password || !createForm.real_name) {
    ElMessage.warning('请填写完整的账号、密码和姓名');
    return;
  }
  creating.value = true;
  try {
    await api.post('/admin/users', createForm);
    ElMessage.success(`医生账号 '${createForm.username}' 开通成功！`);
    showCreateDialog.value = false;
    createForm.username = '';
    createForm.password = '';
    createForm.real_name = '';
    await fetchUsers();
  } catch (err) {
    // Handled
  } finally {
    creating.value = false;
  }
};

const openResetPwd = (user: any) => {
  selectedUser.value = user;
  newPassword.value = '';
  showResetDialog.value = true;
};

const submitResetPassword = async () => {
  if (!newPassword.value) {
    ElMessage.warning('请输入新密码');
    return;
  }
  resetting.value = true;
  try {
    await api.put(`/admin/users/${selectedUser.value.id}/reset-password`, {
      new_password: newPassword.value
    });
    ElMessage.success(`用户 '${selectedUser.value.username}' 密码重置成功！`);
    showResetDialog.value = false;
  } catch (err) {
    // Handled
  } finally {
    resetting.value = false;
  }
};

const formatDate = (dateStr: string) => {
  if (!dateStr) return '尚未登录';
  return new Date(dateStr).toLocaleString('zh-CN', { hour12: false });
};

onMounted(() => {
  fetchUsers();
});
</script>
