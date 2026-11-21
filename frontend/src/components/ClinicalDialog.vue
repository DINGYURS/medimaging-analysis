<template>
  <dialog ref="dialog" class="c-dialog" :class="{ 'c-drawer':drawer, 'c-dialog-closing':closing }" :aria-label="title" :aria-busy="busy" @close="closed" @cancel="cancelled" @click="backdrop">
    <header class="c-dialog-head"><div><h2>{{ title }}</h2><p v-if="subtitle" class="c-muted c-small">{{ subtitle }}</p></div><button class="c-button c-ghost c-icon" aria-label="关闭" :disabled="busy" @click="requestClose">×</button></header>
    <div class="c-dialog-body"><slot /></div>
    <footer v-if="$slots.footer" class="c-dialog-foot"><slot name="footer" /></footer>
  </dialog>
</template>
<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
const props=defineProps<{modelValue:boolean;title:string;subtitle?:string;drawer?:boolean;busy?:boolean}>();
const emit=defineEmits<{ 'update:modelValue':[value:boolean] }>();
const dialog=ref<HTMLDialogElement|null>(null);
const closing=ref(false);
let motion:Animation|null=null,generation=0;
async function sync(){
 const version=++generation;await nextTick();if(version!==generation)return;
 const el=dialog.value;if(!el)return;
 motion?.cancel();motion=null;closing.value=false;
 const animated=props.drawer&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 if(props.modelValue){
  if(el.open)return;
  el.showModal();
  if(animated){motion=el.animate([{transform:'translateX(100%)',opacity:0},{transform:'translateX(0)',opacity:1}],{duration:260,easing:'cubic-bezier(.22,1,.36,1)'});}
 }else if(el.open){
  if(animated){
   closing.value=true;
   motion=el.animate([{transform:'translateX(0)',opacity:1},{transform:'translateX(100%)',opacity:0}],{duration:220,easing:'cubic-bezier(.4,0,1,1)',fill:'forwards'});
   try{await motion.finished;}catch{return;}
   if(version!==generation)return;
  }
  el.close();motion?.cancel();motion=null;closing.value=false;
 }
}
onBeforeUnmount(()=>{generation++;motion?.cancel();});
watch(()=>props.modelValue,sync);onMounted(sync);
function closed(){if(props.modelValue)emit('update:modelValue',false);}
function requestClose(){if(!props.busy)emit('update:modelValue',false);}
function cancelled(event:Event){event.preventDefault();requestClose();}
function backdrop(event:MouseEvent){if(event.target!==dialog.value)return;const b=dialog.value.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)requestClose();}
</script>
