<template>
 <div class="c-processing-grid c-parameters">
  <section class="c-parameter-group">
   <h3 class="c-parameter-label">空间网格重采样</h3>
   <el-select v-model="form.resampling_mode" aria-label="空间网格重采样" class="c-parameter-control">
    <el-option label="各向同性 1.0 × 1.0 × 1.0 mm" value="isotropic_1mm" />
    <el-option label="保留原生网格（不重采样）" value="native" />
    <el-option label="自定义间距" value="custom" />
   </el-select>
   <div v-if="form.resampling_mode==='custom'" class="c-parameter-extra c-spacing-inputs">
    <label v-for="(_,index) in form.custom_spacing" :key="index" class="c-axis-field"><span>{{ ['X','Y','Z'][index] }} <span class="c-muted">(mm)</span></span><el-input-number v-model="form.custom_spacing[index]" :min="0.1" :max="10" :step="0.1" :precision="1" controls-position="right" :aria-label="['X 间距','Y 间距','Z 间距'][index]" /></label>
   </div>
  </section>
  <section class="c-parameter-group">
   <h3 class="c-parameter-label c-parameter-heading">灰度离散化<span class="c-parameter-note">固定分箱宽度（FBS）</span></h3>
   <div class="c-bin-control"><el-input-number v-model="form.bin_width" :min="1" :max="100" :step="5" controls-position="right" aria-label="固定分箱宽度" /><span class="c-muted">HU</span></div>
  </section>
  <section class="c-parameter-group">
   <h3 class="c-parameter-label c-parameter-heading">强度重分割<span v-if="!form.resegmentation_enabled" class="c-parameter-note">使用全部 ROI 强度范围</span></h3>
   <div class="c-resegment-toggle"><el-switch v-model="form.resegmentation_enabled" aria-label="启用强度重分割" /><span>{{ form.resegmentation_enabled?'已启用':'未启用' }}</span></div>
   <div v-if="form.resegmentation_enabled" class="c-parameter-extra c-range-inputs">
    <label class="c-axis-field"><span>下限 <span class="c-muted">(HU)</span></span><el-input-number v-model="form.resegment_min_hu" :step="50" controls-position="right" aria-label="重分割下限" /></label>
    <label class="c-axis-field"><span>上限 <span class="c-muted">(HU)</span></span><el-input-number v-model="form.resegment_max_hu" :step="50" controls-position="right" aria-label="重分割上限" /></label>
   </div>
  </section>
 </div>
</template>
<script setup lang="ts">


defineProps<{form:{resampling_mode:string;custom_spacing:number[];interpolator:string;bin_width:number;resegmentation_enabled:boolean;resegment_min_hu:number;resegment_max_hu:number}}>();
</script>

<style scoped>
.c-parameter-heading{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px}
.c-parameter-heading .c-parameter-note{margin-top:0;font-weight:400}
</style>
