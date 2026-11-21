export interface AnalysisTask {
  id?: number; task_id?: number; task_code?: string; status: string;
  stage_message?: string; progress?: number; queue_position?: number; error_message?: string;
  created_at?: string; finished_at?: string; total_execution_time?: number;
}
export interface Study {
  id: number; modality: string; slice_count: number; study_description?: string | null; study_date?: string | null; created_at?: string;
  last_operation_at?: string | null;
  metadata_json?: Record<string, any>; patient?: { patient_id?: string | null } | null; tasks?: AnalysisTask[];
}
export interface Lesion {
  id: number; lesion_index: number; volume_mm3?: number; equivalent_diameter_mm?: number;
  center_coord?: number[]; features?: Record<string, any>; radiomics_summary?: Record<string, any>;
}
export const activeStatuses = ['QUEUED', 'SEGMENTING', 'EXTRACTING'];
export const taskId = (task?: AnalysisTask | null) => Number(task?.task_id ?? task?.id) || null;
export const isActive = (task?: AnalysisTask | null) => !!task && activeStatuses.includes(task.status);
export function sortedTasks(study?: Study | null) {
  return [...(study?.tasks ?? [])].sort((a,b) => (Date.parse(serverDate(b.created_at)) || 0) - (Date.parse(serverDate(a.created_at)) || 0) || (taskId(b) || 0) - (taskId(a) || 0));
}
export const latestTask = (study?: Study | null) => sortedTasks(study)[0] ?? null;
export const runningTask = (study?: Study | null) => sortedTasks(study).find(isActive) ?? null;
export const displayTask = (study?: Study | null) => runningTask(study) ?? latestTask(study);
export function studyCode(id: number) { return String(id); }
export function serverDate(value?: string) {
  if (!value) return '';
  return value.length > 10 && !/(Z|[+-]\d\d:\d\d)$/i.test(value) ? value + 'Z' : value;
}
export function compareStudies(a: Study, b: Study, order: string) {
  if (order === 'last_operation') {
    // The API sends fixed-width UTC timestamps, including microseconds.
    const aOperation = a.last_operation_at ?? '';
    const bOperation = b.last_operation_at ?? '';
    if (aOperation !== bOperation) return aOperation > bOperation ? -1 : 1;
  }
  const importOrder = (Date.parse(serverDate(a.created_at)) || 0)
    - (Date.parse(serverDate(b.created_at)) || 0) || a.id - b.id;
  return order === 'import_asc' ? importOrder : -importOrder;
}

export function dateTime(value?: string) {
  if (!value) return '—';
  const date = new Date(serverDate(value));
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', { timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false }).format(date);
}
export function studyDate(value?: string | null) {
  if (!value) return '无';
  const date = value.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : '无';
}
export function statusLabel(task?: AnalysisTask | null) {
  if (!task) return '未分析';
  return ({ QUEUED:'等待处理', SEGMENTING:'病灶分析中', EXTRACTING:'结果整理中', COMPLETED:'分析完成', FAILED:'分析失败' } as Record<string,string>)[task.status] || '状态待确认';
}
export function statusTone(task?: AnalysisTask | null) {
  return !task ? 'neutral' : task.status === 'COMPLETED' ? 'success' : task.status === 'FAILED' ? 'danger' : task.status === 'QUEUED' ? 'warning' : isActive(task) ? 'active' : 'neutral';
}
export function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
export function numberText(value: unknown, precision = 1, positive = false) {
  const n = finite(value);
  return n === null || (positive && n <= 0) ? '—' : n.toLocaleString('zh-CN',{ maximumFractionDigits:precision });
}
export function metric(lesion: Lesion | null, key: 'diameter'|'volume'|'equivalent'|'mean'|'sphericity') {
  if (!lesion) return null;
  const f = lesion.features || {}, raw = f.all_raw_features || {};
  const values: Record<string,unknown> = {
    diameter: raw.original_shape_Maximum3DDiameter ?? f.morphology?.max_3d_diameter_mm,
    volume: lesion.volume_mm3,
    equivalent: lesion.equivalent_diameter_mm,
    mean: raw.original_firstorder_Mean,
    sphericity: raw.original_shape_Sphericity ?? f.morphology?.sphericity
  };
  const result = finite(values[key]);
  return key !== 'mean' && result !== null && result <= 0 ? null : result;
}
export interface Geometry { size:number[]; spacing:number[]; origin:number[]; direction:number[]; }
export function geometry(study?: Study | null): Geometry | null {
  const m = study?.metadata_json || {};
  const size=m.volume_size_xyz, spacing=m.volume_spacing_xyz, origin=m.volume_origin_xyz, direction=m.volume_direction;
  if (![size,spacing,origin].every(v=>Array.isArray(v)&&v.length===3&&v.every((n:unknown)=>finite(n)!==null)) || !Array.isArray(direction)||direction.length!==9||direction.some((n:unknown)=>finite(n)===null)) return null;
  if (size.some((n:number)=>n<=0||!Number.isInteger(n)) || spacing.some((n:number)=>n<=0) || size[2]!==study?.slice_count) return null;
  for(let a=0;a<3;a++) for(let b=0;b<3;b++){
    let dot=0; for(let r=0;r<3;r++) dot+=direction[r*3+a]*direction[r*3+b];
    if(Math.abs(dot-(a===b?1:0))>0.001) return null;
  }
  return {size,spacing,origin,direction};
}
export function locateLesion(study: Study | null, lesion: Lesion) {
  const g=geometry(study), p=lesion.center_coord;
  if(!g || !Array.isArray(p)||p.length!==3||p.some(n=>finite(n)===null)) return null;
  const delta=p.map((n,i)=>n-g.origin[i]);
  const point=[0,1,2].map(c=>g.direction[c]*delta[0]/g.spacing[c]+g.direction[3+c]*delta[1]/g.spacing[c]+g.direction[6+c]*delta[2]/g.spacing[c]);
  if(point.some((n,i)=>!Number.isFinite(n)||n<-.5||n>=g.size[i]-.5)) return null;
  const voxel=point.map((n,i)=>Math.min(g.size[i]-1,Math.max(0,Math.round(n))));
  return { slice:voxel[2]+1, x:(point[0]+.5)/g.size[0]*100, y:(point[1]+.5)/g.size[1]*100 };
}
export function planeLabels(study?: Study | null) {
  const g=geometry(study); if(!g) return null;
  const axis=(column:number,sign:number) => {
    const v=[0,1,2].map(r=>g.direction[r*3+column]*sign);
    let i=0; for(let j=1;j<3;j++) if(Math.abs(v[j])>Math.abs(v[i])) i=j;
    if(Math.abs(v[i])<.9) return '';
    return v[i]>0?['L','P','S'][i]:['R','A','I'][i];
  };
  const normal=axis(2,1);
  return {left:axis(0,-1),right:axis(0,1),top:axis(1,-1),bottom:axis(1,1),plane:normal==='S'||normal==='I'?'轴位':normal==='A'||normal==='P'?'冠状位':normal==='L'||normal==='R'?'矢状位':'原始层面'};
}
export function publicError(error: unknown, fallback: string) {
  const value = error as any;
  const detail = value?.response?.data?.detail;
  return typeof detail === 'string' && !/Traceback|\/data\/|patient|Patient|姓名/i.test(detail) ? detail.slice(0,180) : fallback;
}

export function studyHeading(study: Study) {
  const patientId = study.patient?.patient_id?.trim();
  return patientId && patientId.toLowerCase() !== 'unknown' ? patientId + ' · 检查 #' + studyCode(study.id) : '检查 #' + studyCode(study.id);
}

export function calculationTime(value?: string) {
  if (!value) return '—';
  const normalized = value.trim().replace(/ UTC$/i, 'Z').replace(/^(\d{4}-\d{2}-\d{2}) /, '$1T');
  const date = new Date(serverDate(normalized));
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
}
