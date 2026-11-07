<template>
  <dialog ref="dialog" class="c-dialog" :class="{ 'c-drawer':drawer }" :aria-label="title" :aria-busy="busy" @close="closed" @cancel="cancelled" @click="backdrop">
    <header class="c-dialog-head"><div><h2>{{ title }}</h2><p v-if="subtitle" class="c-muted c-small">{{ subtitle }}</p></div><button class="c-button c-ghost c-icon" aria-label="关闭" :disabled="busy" @click="requestClose">×</button></header>
    <div class="c-dialog-body"><slot /></div>
    <footer v-if="$slots.footer" class="c-dialog-foot"><slot name="footer" /></footer>
  </dialog>
</template>
<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue';
const props=defineProps<{modelValue:boolean;title:string;subtitle?:string;drawer?:boolean;busy?:boolean}>();
const emit=defineEmits<{ 'update:modelValue':[value:boolean] }>();
const dialog=ref<HTMLDialogElement|null>(null);
async function sync(){await nextTick(); if(props.modelValue&&!dialog.value?.open)dialog.value?.showModal();else if(!props.modelValue&&dialog.value?.open)dialog.value?.close();}
watch(()=>props.modelValue,sync);onMounted(sync);
function closed(){if(props.modelValue)emit('update:modelValue',false);}
function requestClose(){if(!props.busy)emit('update:modelValue',false);}
function cancelled(event:Event){event.preventDefault();requestClose();}
function backdrop(event:MouseEvent){if(event.target!==dialog.value)return;const b=dialog.value.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)requestClose();}
</script>
