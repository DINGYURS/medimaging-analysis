<template>
  <section class="c-result-block ct-report" aria-label="胸部 CT 初步阅片草稿">
    <header class="report-heading">
      <h2>{{ report.fact_checked ? "胸部 CT 影像报告" : "胸部 CT 初步阅片草稿" }}</h2>
      <div class="report-actions">
        <span v-if="generationStatus" class="c-badge neutral" role="status">{{ generationStatus }}</span>
        <slot name="actions" />
      </div>
    </header>
    <dl v-if="report.exam" class="report-meta"><div><dt>检查信息</dt><dd>{{ report.exam }}</dd></div><div><dt>检查技术</dt><dd>{{ report.technique }}</dd></div></dl>
    <section v-if="report.impression.length" class="ct-impression">
      <h3>影像学印象</h3>
      <ol style="padding-left:20px">
        <li v-for="item in report.impression" :key="item">{{ clinicalText(item) }}</li>
      </ol>
    </section>
    <section class="c-insight-section">
      <h3>影像所见</h3>
      <article v-if="effusionFinding" class="finding-card">
        <h4>胸腔与心包</h4>
        <p>{{ effusionFinding }}</p>
      </article>
      <article v-for="row in report.findings" :key="row.lesion_id" class="finding-card">
        <h4>{{ row.lesion_id }} · {{ row.location || '肺叶待定位' }}</h4>
        <p>{{ clinicalText(row.text) }}</p>
        <button v-if="row.anchor" class="c-link" @click="$emit('locate', row.lesion_id, { ...row.anchor, label: '病灶候选区域' })">
          定位{{ row.lesion_id }} · 第 {{ row.anchor.slice }} 层
        </button>
        <template v-for="hint in row.model_hints" :key="hint.id">
          <button v-if="hint.anchor" class="c-link" style="margin-left:8px"
                  @click="$emit('locate', row.lesion_id, { ...hint.anchor, label: hint.id.endsWith(':spiculation') ? '毛刺候选区域' : '病灶候选区域' })">
            {{ hint.id.endsWith(':spiculation') ? '定位可疑毛刺影' : '定位相关征象' }} · 第 {{ hint.anchor.slice }} 层
          </button>
        </template>
      </article>
      <p v-if="!report.findings.length && !effusionFinding" class="c-muted">本次缺少可供描述的病灶依据。</p>
    </section>
    <details v-if="report.review.length" class="c-insight-section">
      <summary>待复核事项</summary>
      <ul style="padding-left:20px">
        <li v-for="item in report.review" :key="item.text">
          {{ item.text }}
          <button v-if="item.anchor && item.lesion_id" class="c-link"
                  @click="$emit('locate', item.lesion_id, { ...item.anchor, label: item.text.includes('毛刺候选') ? '毛刺候选区域' : '病灶候选区域' })">查看第 {{ item.anchor.slice }} 层</button>
        </li>
      </ul>
    </details>
    <details v-if="report.whole_chest_research.length || report.research_status.length" class="c-insight-section">
      <summary>研究验证记录</summary>
      <p v-for="item in report.research_status" :key="item.name" class="c-small">{{ item.name }} · {{ item.summary }}</p>
      <div v-for="region in report.whole_chest_research" :key="region.id" class="c-small" style="margin-top:6px">
        <span>{{ region.label }} · {{ region.text }}</span>
        <button v-if="region.anchor" class="c-link" style="margin-left:8px" @click="$emit('locateTrial', region.anchor)">
          查看第 {{ region.anchor.slice }} 层
        </button>
      </div>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { clinicalText, formatEffusions } from "./reportEffusions";
export type ReportAnchor = { slice:number; box_xy:[number, number, number, number]; label?:string };
export interface CTReportPreview {
  fact_checked?:boolean; exam?:string; technique?:string; effusions?:string[];
  status:string;
  basis_insight_id?:number;
  generated_by:string;
  findings:{ lesion_id:string; location:string|null; text:string; anchor:ReportAnchor|null;
             model_hints:{id:string; label:string; model:string; status:string; text:string|null; anchor:ReportAnchor|null}[] }[];
  impression:string[];
  review:{text:string; lesion_id?:string; anchor?:ReportAnchor|null}[];
  limitations:string[];
  whole_chest_research:{id:string; label:string; text:string; anchor:ReportAnchor|null}[];
  research_status:{name:string; summary:string}[];
}
const props = defineProps<{report:CTReportPreview; generationStatus?:string}>();
const effusionFinding = computed(() => formatEffusions(props.report.effusions));
defineEmits<{locate:[id:string, anchor:ReportAnchor]; locateTrial:[anchor:ReportAnchor]}>();
</script>


<style scoped>
.report-heading { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px 12px; }
.report-heading h2 { margin:0; }
.report-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-inline-start:auto; }
.ct-report { min-width:0; }
.ct-report h2 { font-size:1.14rem; letter-spacing:.02em; }
.report-meta { display:grid; gap:.45rem; margin:1rem 0; font-size:.82rem; }
.report-meta div { display:grid; grid-template-columns:4.5em 1fr; gap:.7rem; }
.report-meta dt { color:var(--el-text-color-secondary); }
.report-meta dd { margin:0; }
.ct-impression { margin:1rem 0; padding:.85rem 1rem; border-inline-start:3px solid var(--el-color-primary); background:var(--el-fill-color-light); border-radius:4px; }
.ct-impression ol { margin:.55rem 0 0; line-height:1.7; }
.finding-card { border-top:1px solid var(--el-border-color-lighter); padding:.9rem 0; }
.finding-card h4 { margin:0 0 .45rem; }
.finding-card p { line-height:1.75; overflow-wrap:anywhere; }

</style>
