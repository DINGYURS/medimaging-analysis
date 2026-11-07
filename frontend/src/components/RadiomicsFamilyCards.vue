<template>
<div class="c-family-grid">
            <div
              v-for="fam in families"
              :key="fam.id"
              class="c-family-card border rounded-xl p-3 bg-white"
              :class="isFamilySelected(fam.id) ? 'border-blue-200 bg-blue-50/20' : 'border-slate-200'"
            >
              <div class="flex items-start justify-between">
                <el-checkbox
                  :model-value="isFamilySelected(fam.id)"
                  @change="toggleFamily(fam.id)"
                  class="!font-bold !text-slate-800"
                >
                  <span class="text-xs font-bold text-slate-900">{{ fam.name }}</span>
                </el-checkbox>
                <el-tag size="small" type="info" class="!font-mono !text-[10px]">
                  {{ fam.count }} 项
                </el-tag>
              </div>
              <p class="text-[11px] text-slate-500 mt-1 pl-6 leading-relaxed">{{ fam.description }}</p>

              <!-- Collapsible Feature List -->
              <el-collapse class="c-feature-disclosure">
                <el-collapse-item :title="`查看指标明细 (${fam.count} 项)`" :name="fam.id">
                  <div class="space-y-1.5 max-h-40 overflow-y-auto pr-1 text-[11px]">
                    <div
                      v-for="feat in fam.features"
                      :key="feat.key"
                      class="flex items-center justify-between py-0.5 border-b border-slate-50"
                    >
                      <span class="text-slate-700 font-medium truncate max-w-[180px]" :title="feat.definition">
                        {{ feat.label }}
                      </span>
                      <span class="font-mono text-[10px] text-indigo-600 bg-indigo-50 px-1 rounded">
                        {{ feat.ibsi_code || 'IBSI' }}
                      </span>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
          </div>
</template>
<script setup lang="ts">
const props=defineProps<{families:any[];selectedFamilies:string[]}>();
const emit=defineEmits<{toggle:[id:string]}>();
function isFamilySelected(id:string){return props.selectedFamilies.includes(id);}
function toggleFamily(id:string){emit('toggle',id);}
</script>
