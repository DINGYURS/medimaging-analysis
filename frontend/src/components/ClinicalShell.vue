<template>
  <div class="clinical-ui" :data-appearance="resolved">
    <header class="clinical-header">
      <div class="clinical-brand"><BrandMark /><div><strong>医学影像分析</strong><small>MEDIMAGING ANALYSIS</small></div></div>
      <div class="clinical-header-actions">
        <button class="clinical-theme-switch" type="button" role="switch" :aria-checked="resolved==='dark'" aria-label="深色模式" :title="resolved==='dark'?'切换为浅色':'切换为深色'" @click="toggleTheme"><span class="clinical-theme-thumb"></span><el-icon class="clinical-theme-sun" aria-hidden="true"><Sunny /></el-icon><el-icon class="clinical-theme-moon" aria-hidden="true"><Moon /></el-icon></button>
        <template v-if="!publicPage"><span class="clinical-header-divider"></span><div class="clinical-account"><span>{{ auth.user?.real_name || auth.user?.username }}</span></div><button class="c-button c-ghost" @click="auth.logout()">退出</button></template>
      </div>
    </header>
    <slot />
  </div>
</template>
<script setup lang="ts">
import BrandMark from '@/components/BrandMark.vue';
import { computed } from 'vue';
import { Sunny, Moon } from '@element-plus/icons-vue';
import { useAuthStore } from '@/stores/auth';
import { appearance, systemDark, setAppearance } from '@/ui/useAppearance';
import '@/ui/clinical.css';
defineProps<{publicPage?:boolean}>();
const auth=useAuthStore();
const resolved=computed(()=>appearance.value==='system'?(systemDark.value?'dark':'light'):appearance.value);
function toggleTheme(){setAppearance(resolved.value==='dark'?'light':'dark');}
</script>
