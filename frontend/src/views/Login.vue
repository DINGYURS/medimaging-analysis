<template>
  <ClinicalShell public-page>
    <main class="c-login-main"><section class="c-login-card" aria-labelledby="login-title"><BrandMark /><h1 id="login-title">登录医生工作空间</h1><p class="c-muted">检查管理、病灶分析与影像组学。</p>
      <form @submit.prevent="login" :aria-busy="loading"><label class="c-field">登录账号<input v-model="username" class="c-input" autocomplete="username" required maxlength="64" placeholder="请输入登录账号" :disabled="loading"></label><label class="c-field">登录密码<div class="c-password-field"><input v-model="password" class="c-input" :type="showPassword?'text':'password'" autocomplete="current-password" required placeholder="请输入密码" :disabled="loading"><button type="button" class="c-button c-ghost c-password-toggle" :disabled="loading" :aria-pressed="showPassword" :aria-label="showPassword?'隐藏密码':'显示密码'" :title="showPassword?'隐藏密码':'显示密码'" @click="showPassword=!showPassword"><el-icon :size="19" aria-hidden="true"><View v-if="showPassword" /><Hide v-else /></el-icon></button></div></label><p v-if="error" role="alert" class="c-alert" style="margin-top:16px">{{ error }}</p><button class="c-button c-primary" type="submit" :disabled="loading">{{ loading?'正在登录…':'登录' }}</button></form>
      <p class="c-muted" style="margin-top:20px;text-align:center">还没有账号？<RouterLink to="/register">注册医生账号</RouterLink></p>
    </section></main>
  </ClinicalShell>
</template>
<script setup lang="ts">
import BrandMark from '@/components/BrandMark.vue';
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { View, Hide } from '@element-plus/icons-vue';
import ClinicalShell from '@/components/ClinicalShell.vue';
import { useAuthStore } from '@/stores/auth';
const auth=useAuthStore(),router=useRouter();
const username=ref(''),password=ref(''),showPassword=ref(false),loading=ref(false),error=ref('');
async function login(){if(loading.value||!username.value.trim()||!password.value)return;loading.value=true;error.value='';try{await auth.login(username.value.trim(),password.value);await router.push('/dashboard');}catch{error.value='登录未完成，请核对账号、密码或联系管理员。';}finally{loading.value=false;}}
</script>

<style scoped>
.c-password-field{position:relative;width:100%}
.c-password-field .c-input{width:100%;padding-right:48px}
.c-password-field .c-password-toggle{position:absolute;right:4px;top:50%;transform:translateY(-50%);width:36px;height:34px;min-height:0;padding:0;display:flex;align-items:center;justify-content:center}
</style>
