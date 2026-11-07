<template>
  <ClinicalShell><main class="c-main">
    <div class="c-pagehead"><div><h1>医生账号管理</h1><p>管理医生账号、访问状态与登录凭据。</p></div><div class="c-row"><router-link class="c-button c-ghost" to="/dashboard"><el-icon><ArrowLeft /></el-icon>检查列表</router-link><button class="c-button c-primary" @click="showCreate=true"><el-icon><Plus /></el-icon>开通医生账号</button></div></div>
    <p v-if="error" class="c-alert" role="alert" style="margin-bottom:18px">{{ error }} <button class="c-link" @click="fetchUsers">重试</button></p>
    <section class="c-panel" :aria-busy="loading"><div class="c-panel-head"><div class="c-between"><h2>账号列表 <span class="c-small c-muted">{{ loaded?users.length:'—' }}</span></h2><div class="c-row"><label class="c-search" style="margin:0"><el-icon><Search /></el-icon><input v-model="search" aria-label="搜索账号或医生" placeholder="搜索账号或医生"></label><button class="c-button c-icon" :disabled="loading" aria-label="刷新账号列表" @click="fetchUsers"><el-icon><Refresh /></el-icon></button></div></div></div>
      <div class="c-table-wrap"><table class="c-table"><thead><tr><th>登录账号</th><th>医生姓名</th><th>系统角色</th><th>账号状态</th><th>最后登录</th><th>操作</th></tr></thead><tbody><tr v-for="user in filtered" :key="user.id"><td class="c-code">{{ user.username }}</td><td>{{ user.real_name }}</td><td><span class="c-badge neutral">{{ user.role==='ADMIN'?'管理员':'医生' }}</span></td><td><span class="c-badge" :class="user.is_active?'success':'neutral'">{{ user.is_active?'已启用':'已禁用' }}</span></td><td class="c-muted c-numeric">{{ user.last_login_at?dateTime(user.last_login_at):'尚未登录' }}</td><td><div class="c-table-actions"><button class="c-link" @click="openReset(user)">重置密码</button><button class="c-link c-muted" :disabled="user.id===auth.user?.id" @click="selected=user;showStatus=true">{{ user.is_active?'禁用':'启用' }}</button></div></td></tr><tr v-if="!filtered.length"><td colspan="6"><div class="c-empty">{{ loading?'正在读取账号…':loaded?'没有符合条件的账号。':'账号列表暂不可用。' }}</div></td></tr></tbody></table></div><div class="c-table-foot">共 {{ loaded?filtered.length:'—' }} 个账号</div>
    </section>
    <ClinicalDialog v-model="showCreate" :busy="writing" title="开通医生账号"><form id="create-account" @submit.prevent="createUser"><div class="c-form-grid"><label class="c-field">登录账号<input v-model="form.username" class="c-input" required maxlength="64" autocomplete="off"></label><label class="c-field">医生姓名<input v-model="form.real_name" class="c-input" required autocomplete="off"></label><label class="c-field full">初始密码<input v-model="form.password" class="c-input" type="password" required autocomplete="new-password"></label></div></form><template #footer><button class="c-button" :disabled="writing" @click="showCreate=false">取消</button><button class="c-button c-primary" form="create-account" type="submit" :disabled="writing">{{ writing?'正在创建…':'确认创建' }}</button></template></ClinicalDialog>
    <ClinicalDialog v-model="showReset" :busy="writing" title="重置登录密码" :subtitle="selected?.username"><form id="reset-account" @submit.prevent="resetPassword"><label class="c-field">新密码<input v-model="newPassword" class="c-input" type="password" required autocomplete="new-password"></label></form><template #footer><button class="c-button" :disabled="writing" @click="showReset=false">取消</button><button class="c-button c-primary" type="submit" form="reset-account" :disabled="writing">{{ writing?'正在保存…':'确认重置' }}</button></template></ClinicalDialog>
    <ClinicalDialog v-model="showStatus" :busy="writing" :title="selected?.is_active?'禁用账号':'启用账号'" :subtitle="selected?.username"><p class="c-muted">{{ selected?.is_active?'禁用后，该账号将无法登录系统。':'启用后，该账号可重新登录系统。' }}</p><template #footer><button class="c-button" :disabled="writing" @click="showStatus=false">取消</button><button class="c-button c-primary" :disabled="writing" @click="toggleStatus">{{ writing?'正在保存…':'确认' }}</button></template></ClinicalDialog>
  </main></ClinicalShell>
</template>
<script setup lang="ts">
import { ref,reactive,computed,onMounted } from 'vue';
import { ArrowLeft,Plus,Search,Refresh } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import ClinicalShell from '@/components/ClinicalShell.vue';
import ClinicalDialog from '@/components/ClinicalDialog.vue';
import api from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import { dateTime } from '@/ui/clinical';
const auth=useAuthStore(),users=ref<any[]>([]),loading=ref(false),loaded=ref(false),error=ref(''),search=ref('');
const filtered=computed(()=>{const term=search.value.trim().toLowerCase();return users.value.filter(u=>[u.username,u.real_name].some(s=>String(s||'').toLowerCase().includes(term)));});
const selected=ref<any>(null),showCreate=ref(false),showReset=ref(false),showStatus=ref(false),writing=ref(false),newPassword=ref('');
const form=reactive({username:'',real_name:'',password:''});
async function fetchUsers(){if(loading.value)return;loading.value=true;try{const data:any=await api.get('/admin/users');users.value=Array.isArray(data)?data:[];loaded.value=true;error.value='';}catch{error.value='账号列表读取失败，请稍后重试。';}finally{loading.value=false;}}
async function createUser(){if(writing.value||!form.username.trim()||!form.real_name.trim()||!form.password)return;writing.value=true;try{await api.post('/admin/users',{...form,username:form.username.trim(),real_name:form.real_name.trim()});showCreate.value=false;form.username='';form.real_name='';form.password='';await fetchUsers();ElMessage.success('医生账号已创建。');}catch{}finally{writing.value=false;}}
function openReset(user:any){selected.value=user;newPassword.value='';showReset.value=true;}
async function resetPassword(){if(!selected.value||!newPassword.value||writing.value)return;writing.value=true;try{await api.put('/admin/users/'+selected.value.id+'/reset-password',{new_password:newPassword.value});showReset.value=false;newPassword.value='';ElMessage.success('登录密码已重置。');}catch{}finally{writing.value=false;}}
async function toggleStatus(){if(!selected.value||selected.value.id===auth.user?.id||writing.value)return;writing.value=true;try{await api.put('/admin/users/'+selected.value.id+'/status?is_active='+!selected.value.is_active);showStatus.value=false;await fetchUsers();ElMessage.success('账号状态已更新。');}catch{}finally{writing.value=false;}}
onMounted(fetchUsers);
</script>
