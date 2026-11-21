<template>
  <section v-if="data && data.status !== 'NOT_ENABLED'" class="c-result-block" :aria-busy="busy">
    <div class="c-between">
      <h2>AI 影像分析参考</h2>
      <span class="c-badge neutral" role="status">{{ labels[data.status] }}</span>
    </div>
    <p v-if="error" class="c-alert" role="alert">{{ error }} <button class="c-link" @click="reload">重新读取</button></p>
    <p v-if="data.status !== 'READY'" class="c-small c-muted" role="status" style="margin-top:12px">
      {{ data.message || descriptions[data.status] }}
    </p>
    <template v-if="data.result">
      <section class="c-insight-section">
        <h3>AI 所见</h3>
        <p>{{ data.result.overview }}</p>
        <div v-if="data.result.findings.length" class="c-reference-findings">
          <div v-for="finding in data.result.findings" :key="finding.id" class="c-reference-finding">
            <span>候选 #{{ finding.lesion_index }} · {{ finding.lobe || finding.side || '肺叶位置未确定' }}</span>
            <span v-if="finding.diameter_mm !== null">轴位长径 {{ finding.diameter_mm.toFixed(1) }} mm</span>
            <button v-if="finding.anchor" class="c-link" @click="$emit('locate', finding.lesion_index, finding.anchor)">
              查看测量层
            </button>
            <p v-if="data.result.attribute_details?.[finding.id]" class="c-reference-detail c-small">
              {{ data.result.attribute_details[finding.id] }}
              <span v-if="finding.attributes?.some(item => item.code === 'density_texture' && item.status === 'indeterminate')">
                密度分类不确定。
              </span>
            </p>
          </div>
        </div>
      </section>
      <section class="c-insight-section"><h3>综合解读</h3><p>{{ data.result.summary }}</p></section>
      <section class="c-insight-section c-small c-muted">
        <h3>分析范围</h3>
        <p v-for="line in data.result.coverage" :key="line">{{ line }}</p>
        <p>{{ data.result.text_source === 'template' ? '基于本地证据整理' : 'DeepSeek V4 Flash 组织文本' }}</p>
      </section>
    </template>
    <button v-if="data.status !== 'RUNNING'" class="c-button c-primary" style="margin-top:14px"
      :disabled="busy" @click="generate">{{ data.result ? '重新生成参考' : '生成参考' }}</button>
  </section>
  <p v-else-if="error" class="c-alert" role="alert">{{ error }} <button class="c-link" @click="reload">重新读取</button></p>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import api from '@/api/client';

type Point = [number, number];
interface Anchor { slice: number; endpoints_xy: [Point, Point] }
interface Finding {
  id: string;
  lesion_index: number;
  lobe: string | null;
  side: string | null;
  diameter_mm: number | null;
  anchor: Anchor | null;
  attributes?: { code: string; status: string }[];
}
type Status = 'NOT_ENABLED' | 'NOT_GENERATED' | 'RUNNING' | 'READY' | 'STALE' | 'BLOCKED' | 'FAILED';
interface Reference {
  status: Status;
  message?: string;
  result?: {
    overview: string;
    summary: string;
    findings: Finding[];
    coverage: string[];
    text_source: string;
    attribute_details?: Record<string, string>;
  };
}

const props = defineProps<{ taskId: number }>();
defineEmits<{ locate: [index: number, anchor: Anchor] }>();
const data = ref<Reference | null>(null);
const error = ref('');
const submitting = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let requestVersion = 0;
const busy = computed(() => submitting.value || data.value?.status === 'RUNNING');
const labels: Record<Status, string> = {
  NOT_ENABLED: '未启用', NOT_GENERATED: '尚未生成', RUNNING: '正在生成', READY: '已生成',
  STALE: '依据已更新', BLOCKED: '资料不足', FAILED: '生成失败',
};
const descriptions: Partial<Record<Status, string>> = {
  NOT_GENERATED: '依据当前 CT 任务生成肺结节候选参考。',
  RUNNING: '正在计算候选位置、径线和文本。',
  STALE: '来源数据已更新，可重新生成。',
};

function apply(result: Reference, version: number) {
  if (version !== requestVersion) return;
  data.value = result;
  error.value = '';
  if (result.status === 'RUNNING') timer = setTimeout(() => void read(version), 3000);
}

async function read(version: number) {
  try {
    apply(await api.get('/insights/' + props.taskId + '/ct-reference') as unknown as Reference, version);
  } catch {
    if (version === requestVersion) error.value = '影像参考读取失败。';
  }
}

function reload() {
  clearTimeout(timer);
  void read(++requestVersion);
}

async function generate() {
  if (busy.value) return;
  submitting.value = true;
  const version = ++requestVersion;
  try {
    apply(await api.post('/insights/' + props.taskId + '/ct-reference') as unknown as Reference, version);
  } catch {
    if (version === requestVersion) error.value = '影像参考提交失败。';
  } finally {
    if (version === requestVersion) submitting.value = false;
  }
}

onMounted(reload);
onBeforeUnmount(() => { requestVersion++; clearTimeout(timer); });
</script>

<style scoped>
.c-reference-findings {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
.c-reference-finding {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
}
.c-reference-detail {
  flex-basis: 100%;
}
</style>
