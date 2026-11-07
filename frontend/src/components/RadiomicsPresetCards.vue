<template>
<div class="c-preset-grid">
            <button type="button" :aria-pressed="activePresetId === preset.id" :disabled="busy"
              v-for="preset in presets"
              :key="preset.id"
              @click="applyPreset(preset)"
              :class="activePresetId === preset.id ? 'border-blue-600 bg-blue-50/70 shadow-xs ring-1 ring-blue-500/30' : 'border-slate-200 bg-white hover:border-slate-300'"
              class="p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between text-left"
            >
              <div>
                <div class="font-bold text-sm text-slate-900 mb-1 leading-snug">{{ preset.name }}</div>
                <div class="text-xs text-slate-500 leading-relaxed">{{ preset.description }}</div>
              </div>
              <div class="mt-2 pt-2 border-t border-slate-100 flex items-center justify-between text-[10px]">
                <span class="text-slate-400">覆盖特征类</span><span class="text-blue-600 font-medium font-mono">
                  {{ preset.config.selected_classes ? preset.config.selected_classes.length : 7 }} 大家族
                </span>
                <el-icon v-if="activePresetId === preset.id" class="text-blue-600"><Check /></el-icon>
              </div>
            </button>
          </div>
</template>
<script setup lang="ts">
import { Check } from '@element-plus/icons-vue';
defineProps<{presets:any[];activePresetId:string;busy:boolean}>();
const emit=defineEmits<{apply:[preset:any]}>();
function applyPreset(preset:any){emit('apply',preset);}
</script>
