<template>
  <ClinicalShell public-page>
    <main class="c-login-main"><section class="c-login-card" aria-labelledby="register-title">
      <BrandMark />
      <h1 id="register-title">注册医生账号</h1><p class="c-muted">创建账号，进入医生工作空间。</p>
      <form @submit.prevent="register" :aria-busy="loading">
        <label class="c-field">登录账号<input v-model="username" class="c-input" autocomplete="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_]+" placeholder="3–64 位字母、数字或下划线" :disabled="loading"></label>
        <label class="c-field">姓名<input v-model="realName" class="c-input" autocomplete="name" required maxlength="64" placeholder="请输入姓名" :disabled="loading"></label>
        <label class="c-field">登录密码<div class="password-field">
          <input v-model="password" class="c-input" :type="showPassword?'text':'password'" autocomplete="new-password" required minlength="8" maxlength="72" placeholder="请输入至少 8 位密码" :disabled="loading">
          <button type="button" class="c-button c-ghost password-toggle" :disabled="loading" :aria-pressed="showPassword" :aria-label="showPassword?'隐藏密码':'显示密码'" :title="showPassword?'隐藏密码':'显示密码'" @click="showPassword=!showPassword"><el-icon :size="19"><View v-if="showPassword"/><Hide v-else/></el-icon></button>
        </div></label>
        <label class="c-field">确认密码<input v-model="confirmation" class="c-input" :type="showPassword?'text':'password'" autocomplete="new-password" required maxlength="72" placeholder="请再次输入密码" :disabled="loading"></label>
        <p v-if="error" class="c-alert" role="alert" style="margin-top:16px">{{ error }}</p>
        <button class="c-button c-primary" type="submit" :disabled="loading">{{ loading?'正在注册…':'注册' }}</button>
      </form>
      <p class="c-muted" style="margin-top:20px;text-align:center">已有账号？<RouterLink to="/login">返回登录</RouterLink></p>
    </section></main>
  </ClinicalShell>
</template>
<script setup lang="ts">
import BrandMark from '@/components/BrandMark.vue';
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { View, Hide } from '@element-plus/icons-vue';
import ClinicalShell from '@/components/ClinicalShell.vue';
import api from '@/api/client';
const router=useRouter();
const username=ref(''),realName=ref(''),password=ref(''),confirmation=ref('');
const showPassword=ref(false),loading=ref(false),error=ref('');
async function register(){
  if(loading.value)return;
  error.value='';
  if(!realName.value.trim()){error.value='请填写姓名。';return;}
  if(password.value!==confirmation.value){error.value='两次输入的密码不一致。';return;}
  if(password.value.length<8||!password.value.trim()){error.value='密码至少需要 8 位，且不能全为空格。';return;}
  if(new TextEncoder().encode(password.value).length>72){error.value='密码过长，请缩短后重试；中文或特殊符号可能占用更多长度。';return;}
  loading.value=true;
  try{
    await api.post('/auth/register',{username:username.value.trim(),real_name:realName.value.trim(),password:password.value});
    ElMessage.success('注册成功，请使用新账号登录');
    await router.replace('/login');
  }catch(err:any){
    const detail=err.response?.data?.detail;
    error.value=typeof detail==='string'?detail:'注册未完成，请检查填写内容或稍后重试。';
  }finally{loading.value=false;}
}
</script>
<style scoped>
.password-field{position:relative;width:100%}
.password-field .c-input{width:100%;padding-right:48px}
.password-toggle{position:absolute;right:4px;top:50%;transform:translateY(-50%);width:36px;height:34px;min-height:0;padding:0;display:flex;align-items:center;justify-content:center}
</style>
