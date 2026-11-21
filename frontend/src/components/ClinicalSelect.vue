<script setup lang="ts">
export interface ClinicalSelectOption { value: string; label: string; description?: string; disabled?: boolean }
defineProps<{modelValue: string; options: ClinicalSelectOption[]}>()
const emit=defineEmits<{'update:modelValue':[value:string];change:[value:string]}>()
</script>
<template>
 <el-select class="clinical-select" :model-value="modelValue" popper-class="clinical-select-popper" @update:model-value="emit('update:modelValue',$event)" @change="emit('change',$event)">
  <el-option v-for="option in options" :key="option.value" :value="option.value" :label="option.label" :disabled="option.disabled">
   <span class="clinical-option-label">{{ option.label }}</span>
   <span v-if="option.description" class="clinical-option-description">{{ option.description }}</span>
  </el-option>
 </el-select>
</template>
