import snapshot from './catalog.json'
import type { RadiomicsConfig, RadiomicsFeatureClass, RadiomicsFeatureDefinition } from './types'

// Generated from source catalogue metadata for frontend review.
// This snapshot describes the migration scope, not current backend capability.
export const radiomicsCatalog = snapshot.features as RadiomicsFeatureDefinition[]
export const catalogSourceHash = snapshot.source_sha256
export const featureFamilies = [
  { id: 'morphology', label: '形态学', short: 'Morphological', branch: 'morphology' },
  { id: 'local_intensity', label: '局部强度', short: 'Local intensity', branch: 'local' },
  { id: 'intensity_statistics', label: '强度统计', short: 'Statistical', branch: 'statistics' },
  { id: 'intensity_histogram', label: '强度直方图', short: 'IH', branch: 'texture' },
  { id: 'intensity_volume_histogram', label: '强度体积直方图', short: 'IVH', branch: 'texture' },
  { id: 'glcm', label: '灰度共生矩阵', short: 'GLCM', branch: 'texture' },
  { id: 'glrlm', label: '灰度游程矩阵', short: 'GLRLM', branch: 'texture' },
  { id: 'glszm', label: '灰度区域大小矩阵', short: 'GLSZM', branch: 'texture' },
  { id: 'gldzm', label: '灰度区域距离矩阵', short: 'GLDZM', branch: 'gldzm' },
  { id: 'ngtdm', label: '邻域灰度差矩阵', short: 'NGTDM', branch: 'texture' },
  { id: 'ngldm', label: '邻域灰度依赖矩阵', short: 'NGLDM', branch: 'texture' },
  { id: 'custom_lung_nodule', label: '自定义增量特征', short: '独立于IBSI标准', branch: 'custom' },
] as const

export interface RadiomicsPlanDraft {
  selectedKeys: string[]
  maskInterpolator: 'sitkNearestNeighbor' | 'sitkLinear'
  maskThreshold: number | string
  resegmentation: boolean
  intensityMin: number | string
  intensityMax: number | string
  intensityKind: 'calibrated' | 'arbitrary' | 'unknown'
  intensityUnit: string
  deviationAcknowledged: boolean
}

export function createPlanDraft(catalog = radiomicsCatalog): RadiomicsPlanDraft {
  return {
    selectedKeys: catalog.filter(feature => feature.status === 'computed' && feature.standard_status !== 'custom').map(feature => feature.key),
    maskInterpolator: 'sitkNearestNeighbor', maskThreshold: 0.5,
    resegmentation: false, intensityMin: '', intensityMax: '',
    intensityKind: 'calibrated', intensityUnit: '', deviationAcknowledged: false,
  }
}

export function planDependencies(keys: string[], catalog = radiomicsCatalog) {
  const selected = catalog.filter(feature => keys.includes(feature.key) && feature.status === 'computed')
  const families = new Set(selected.map(feature => feature.family))
  return {
    count: selected.length,
    families: featureFamilies.filter(family => families.has(family.id)),
    branches: [...new Set(selected.map(feature => feature.calculation_module))],
    morphology: selected.some(feature => feature.required_masks.includes('morphological')),
    intensity: selected.some(feature => feature.required_masks.includes('intensity')),
    custom: selected.some(feature => feature.family === 'custom_lung_nodule'),
    discretisation: selected.some(feature => feature.requires_discretisation),
    histogram: selected.some(feature => feature.discretisation_group === 'intensity_histogram'),
    texture: selected.some(feature => feature.discretisation_group === 'texture'),
    ivh: selected.some(feature => feature.discretisation_group === 'intensity_volume_histogram'),
    localContext: selected.some(feature => feature.requires_local_context),
  }
}

export function derivePyradiomicsClasses(keys: string[], catalog = radiomicsCatalog): RadiomicsFeatureClass[] {
  // Map only corresponding families; GLDZM, NGLDM, local intensity and IVH
  // stay on the IBSI path rather than being presented as PyRadiomics equivalents.
  const classes = new Set<RadiomicsFeatureClass>()
  for (const feature of catalog) {
    if (keys.includes(feature.key) && feature.status === 'computed' && feature.legacy_pyradiomics_class) {
      classes.add(feature.legacy_pyradiomics_class)
    }
  }
  const order: RadiomicsFeatureClass[] = ['firstorder', 'shape', 'glcm', 'glrlm', 'glszm', 'ngtdm']
  return order.filter(item => classes.has(item))
}

const finiteInput = (value: number | string) => value !== '' && Number.isFinite(Number(value))
export function planDraftErrors(draft: RadiomicsPlanDraft, config?: Pick<RadiomicsConfig, 'resampling'>, catalog = radiomicsCatalog): string[] {
  const errors: string[] = []
  if (!planDependencies(draft.selectedKeys, catalog).count) errors.push('至少选择一个可计算的目标特征')
  if (config?.resampling !== 'none' && draft.maskInterpolator === 'sitkLinear' && (!finiteInput(draft.maskThreshold)
    || Number(draft.maskThreshold) <= 0 || Number(draft.maskThreshold) > 1)) errors.push('掩膜二值化阈值须在 (0, 1] 内')
  if (draft.resegmentation && (!finiteInput(draft.intensityMin) || !finiteInput(draft.intensityMax)
    || Number(draft.intensityMin) >= Number(draft.intensityMax))) errors.push('重分割需要有效的上下限，且下限须小于上限')
  if (draft.resegmentation) {
    if (!draft.intensityUnit.trim()) errors.push('请填写或自动匹配重分割的阈值单位')
  }
  return errors
}

export function planExecutionBlockers(config: RadiomicsConfig, draft: RadiomicsPlanDraft, catalog = radiomicsCatalog): string[] {
  const blockers: string[] = []
  const selected = new Set(draft.selectedKeys)
  if (!selected.size || draft.selectedKeys.length !== selected.size
    || [...selected].some(key => !catalog.some(feature => feature.key === key && feature.status === 'computed'))) {
    blockers.push('请选择非空、无重复且受支持的目标特征')
  }
  const custom = draft.selectedKeys.some(key => catalog.some(feature => feature.key === key && feature.family === 'custom_lung_nodule'))
  if (custom && config.normalization === 'z-score') blockers.push('自定义增量特征沿用原 CT 强度公式，请选择不归一化或按模态自适应')
  if (custom && draft.resegmentation) blockers.push('自定义增量特征沿用原单一 ROI 公式，请关闭重分割')
  if (draft.resegmentation && (config.include_liver_features ?? true)) blockers.push('启用重分割时请关闭肝脏补充特征')
  return blockers
}

export function defaultRadiomicsConfig(): RadiomicsConfig {
  return {
    source_version: 'legacy',
    processing_order: 'interpolate_then_normalize',
    feature_classes: derivePyradiomicsClasses(createPlanDraft().selectedKeys),
    resampling: [1, 1, 1], image_interpolator: 'sitkBSpline', normalization: 'auto',
    normalization_scale: 100, include_liver_features: false, bin_width: null,
  }
}

// Serialize only active processing parameters; inactive ranges stay local.
export function executableConfig(config: RadiomicsConfig, draft?: RadiomicsPlanDraft, legacyOutput = false): RadiomicsConfig {
  const maskInterpolator = config.resampling === 'none'
    ? (legacyOutput ? config.mask_interpolator ?? 'sitkNearestNeighbor' : 'sitkNearestNeighbor')
    : (draft?.maskInterpolator ?? config.mask_interpolator ?? 'sitkNearestNeighbor')
  return {
    source_version: config.source_version ?? 'legacy',
    ...(config.discretisation ? { discretisation: {
      intensity_histogram: { ...config.discretisation.intensity_histogram },
      texture: { ...config.discretisation.texture },
      intensity_volume_histogram: { ...config.discretisation.intensity_volume_histogram },
    } } : {}),
    processing_order: config.processing_order ?? 'legacy',
    resegmentation: draft ? (draft.resegmentation ? {
      method: 'range', lower: Number(draft.intensityMin), upper: Number(draft.intensityMax),
      intensity_basis: { kind: draft.intensityKind, unit: draft.intensityUnit.trim() },
      ibsi_deviation_acknowledged: draft.deviationAcknowledged,
    } : { method: 'none' }) : (config.resegmentation?.method === 'range' ? { ...config.resegmentation, intensity_basis: { kind: config.resegmentation.intensity_basis.kind, unit: config.resegmentation.intensity_basis.unit } } : { method: 'none' }),
    ...(!legacyOutput && config.features ? { features: { catalog_version: config.features.catalog_version, keys: [...config.features.keys] } } : {}),
    feature_classes: [...config.feature_classes],
    resampling: config.resampling === 'none' ? 'none' : [...config.resampling],
    image_interpolator: config.image_interpolator ?? 'sitkBSpline',
    mask_interpolator: maskInterpolator,
    mask_threshold: legacyOutput && config.resampling === 'none' ? config.mask_threshold ?? 0.5
      : maskInterpolator === 'sitkLinear' ? Number(draft?.maskThreshold ?? config.mask_threshold ?? 0.5)
        : legacyOutput ? config.mask_threshold ?? 0.5 : 0.5,
    normalization: config.normalization,
    normalization_scale: config.normalization_scale ?? 100,
    bin_width: config.bin_width,
    include_liver_features: config.include_liver_features ?? true,
  }
}

export function configurationFingerprint(filter: unknown, config: RadiomicsConfig, draft: RadiomicsPlanDraft): string {
  return JSON.stringify([filter, executableConfig(config), draft])
}

export function discretisationErrors(config: RadiomicsConfig, keys: string[], catalog = radiomicsCatalog): string[] {
  if (!config.discretisation) return []
  const dependencies = planDependencies(keys, catalog)
  const errors: string[] = []
  for (const [group, label, active] of [
    ['intensity_histogram', 'IH', dependencies.histogram],
    ['texture', 'Texture', dependencies.texture],
    ['intensity_volume_histogram', 'IVH', dependencies.ivh],
  ] as const) {
    const rule = config.discretisation[group]
    if (!active) continue
    if (rule.method === 'none') errors.push(`${label} 需要离散化配置`)
    if (rule.method === 'fbs') {
      if (!(typeof rule.bin_width === 'number' && Number.isFinite(rule.bin_width) && rule.bin_width > 0)) errors.push(`${label} 分箱宽度须为有限正数`)
      if (rule.bin_origin != null && !Number.isFinite(rule.bin_origin)) errors.push(`${label} 显式起点须为有限数值`)
    }
    if (rule.method === 'fbn' && (!Number.isSafeInteger(rule.bin_count) || rule.bin_count <= 0)) errors.push(`${label} 分箱数量须为正整数`)
  }
  return errors
}

export function discretisationSummary(config: RadiomicsConfig, keys: string[], catalog = radiomicsCatalog): string {
  if (!config.discretisation) return `兼容模式FBS ${config.bin_width ?? '自动（CT 25，MR 5）'}；IVH 使用既有专用规则`
  const d = planDependencies(keys, catalog)
  return ([['intensity_histogram', 'IH', d.histogram], ['texture', 'Texture', d.texture], ['intensity_volume_histogram', 'IVH', d.ivh]] as const)
    .filter(([, , active]) => active).map(([group, label]) => {
      const rule = config.discretisation![group]
      if (rule.method === 'fbn') return `${label} 固定分箱数量 ${rule.bin_count} 箱`
      if (rule.method === 'fbs') return `${label} 固定分箱宽度 ${rule.bin_width}，起点 ${rule.bin_origin == null ? '感兴趣区最小值' : rule.bin_origin}`
      return `${label} 自动（${config.source_version === 'physical-v1' ? '非已声明 HU／归一化后' : '非 CT／归一化后'}回退宽度 ${config.bin_width ?? '按模态 CT 25，MR 5'}）`
    }).join('；')
}
