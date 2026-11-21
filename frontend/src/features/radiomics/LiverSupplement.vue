<script setup lang="ts">
defineProps<{result: {status:string;reason?:string;input_mode?:string;features:Record<string,number>;unavailable:Record<string,string>;curves:Record<string,{x:number[];y:number[]}>;definitions?:Record<string,{unit:string;formula:string}>}}>()
</script>
<template>
  <div class="liver-result">
    <p v-if="result.status==='not_applicable'" class="c-muted">肝脏补充：{{ result.reason }}</p>
    <template v-else>
      <p v-if="result.input_mode==='geometry_only_zero_signal'" class="c-muted">纯形态学零强度兼容模式</p>
      <p><strong>肝脏补充</strong> · {{ Object.keys(result.features).length }} 项数值 · {{ Object.keys(result.curves).length }} 组曲线 · {{ Object.keys(result.unavailable).length }} 项不可计算</p>
      <details><summary>查看肝脏补充结果</summary>
        <dl><div v-for="(value,key) in result.features" :key="key"><dt :title="result.definitions?.[key]?.formula">{{ key }}</dt><dd>{{ value }} <span class="c-muted">{{ result.definitions?.[key]?.unit }}</span></dd></div></dl>
        <details v-for="(curve,key) in result.curves" :key="key"><summary>{{ key }} · {{ curve.x.length }} 个点</summary><table><thead><tr><th>x</th><th>CDF</th></tr></thead><tbody><tr v-for="(x,index) in curve.x" :key="index"><td>{{ x }}</td><td>{{ curve.y[index] }}</td></tr></tbody></table></details>
        <details v-if="Object.keys(result.unavailable).length"><summary>查看肝脏补充不可计算原因</summary><p v-for="(reason,key) in result.unavailable" :key="key"><strong>{{ key }}</strong>：{{ reason }}</p></details>
      </details>
    </template>
  </div>
</template>
<style scoped>
.liver-result{flex-basis:100%;min-width:0;margin-top:4px}summary{cursor:pointer;color:var(--c-muted)}dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 20px;padding:12px 0}dt{overflow-wrap:anywhere;color:var(--c-muted)}dd{margin:4px 0 0}details details{margin-top:12px;max-height:280px;overflow:auto}table{width:100%;text-align:left;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid var(--c-line)}p{overflow-wrap:anywhere}@media(max-width:700px){dl{grid-template-columns:1fr}}
</style>
