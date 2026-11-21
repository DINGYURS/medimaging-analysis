import type { RadiomicsConfig } from './types'
import { discretisationSummary, planDependencies, radiomicsCatalog } from './plan'

export interface CalculationRun {
  id: string
  analysis_task_id: number
  status: string
  selected_count: number
  total_lesions: number
  completed_lesions: number
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  config?: RadiomicsConfig
  catalog_version?: string
  engine_version?: string
  configuration_hash?: string
  input_hash?: string
  error_summary?: { reason?: string; unavailable_features?: number } | null
  results?: { lesion_id: number; lesion_index: number; status: string; features: Record<string, number>; unavailable: Record<string, string>; provenance: Record<string, any> }[]
}

export const activeStatus = (status: string) => ['pending', 'running'].includes(status)
export const exportable = (run?: CalculationRun | null) => !!run && ['succeeded', 'partial'].includes(run.status)
export function statusLabel(status: string) {
  return ({ pending: '等待计算', running: '计算中', succeeded: '计算完成', partial: '部分成功', failed: '计算失败', interrupted: '已中断' } as Record<string, string>)[status] || status
}
export function recordTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(date)
}
export function gridSummary(config?: RadiomicsConfig) {
  return !config ? '—' : config.resampling === 'none' ? '原生网格' : config.resampling.join(' × ') + ' mm'
}
export function normalizationSummary(config?: RadiomicsConfig) {
  if (!config) return '—'
  return config.normalization === 'none' ? '不归一化' : `${config.normalization === 'auto' ? '按模态自适应' : 'Z-Score'} × ${config.normalization_scale ?? 100}`
}
export function normalizationAndOrderSummary(run: CalculationRun) {
  const config = run.config
  if (!config) return '—'
  if (config.normalization === 'none') return '不归一化（处理顺序不适用）'
  const requested = normalizationSummary(config)
  const methods = (run.results || []).map(row => row.provenance?.processing?.normalization?.method).filter(Boolean)
  if (methods.length && methods.every(method => method === 'none')) return requested + ' · 当次未归一化（处理顺序不适用）'
  if (config.resampling === 'none') return requested + ' · 原生网格归一化（未执行插值）'
  if (config.resegmentation?.method === 'range') return requested + ' · 在插值、重分割后归一化'
  return requested + ' · ' + (config.processing_order === 'interpolate_then_normalize' ? '在插值后归一化' : '在插值前归一化')
}
export function binsSummary(config?: RadiomicsConfig) {
  if (!config) return '—'
  return planDependencies(config.features?.keys || [], radiomicsCatalog).discretisation ? discretisationSummary(config, config.features?.keys || [], radiomicsCatalog) : '无需IBSI离散化'
}
export function familiesSummary(config?: RadiomicsConfig) {
  const names: Record<string, string> = { morphology: 'MORPH', local_intensity: 'LI', intensity_statistics: 'STAT', intensity_histogram: 'IH', intensity_volume_histogram: 'IVH', glcm: 'GLCM', glrlm: 'GLRLM', glszm: 'GLSZM', gldzm: 'GLDZM', ngtdm: 'NGTDM', ngldm: 'NGLDM', custom_lung_nodule: 'CUSTOM' }
  return config ? planDependencies(config.features?.keys || [], radiomicsCatalog).families.map(f => names[f.id]).join(' / ') || '—' : '—'
}
export function interpolationSummary(config?: RadiomicsConfig) {
  if (!config) return '—'
  if (config.resampling === 'none') return '不执行插值'
  const names: Record<string, string> = { sitkBSpline: 'B-Spline', sitkLinear: 'Trilinear', sitkNearestNeighbor: 'Nearest Neighbor' }
  return `影像：${names[config.image_interpolator || 'sitkBSpline']}；ROI：${names[config.mask_interpolator || 'sitkNearestNeighbor']}${config.mask_interpolator === 'sitkLinear' ? `，阈值 ${config.mask_threshold ?? 0.5}` : ''}`
}
export function resegmentationSummary(config?: RadiomicsConfig) {
  const range = config?.resegmentation
  return range?.method === 'range' ? `[${range.lower}, ${range.upper}] · ${range.intensity_basis.unit}` : '关闭'
}
export function durationSummary(run: CalculationRun) {
  if (!run.started_at || !run.finished_at) return '—'
  const seconds = (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
  return Number.isFinite(seconds) && seconds >= 0 ? `${seconds.toFixed(2)} 秒` : '—'
}
export function featureName(key: string) {
  return radiomicsCatalog.find(row => row.key === key)?.name || key
}
