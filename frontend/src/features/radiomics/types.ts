// Frontend review contract; no calculation endpoint is connected yet.
export type RadiomicsFeatureClass =
  | 'firstorder'
  | 'shape'
  | 'glcm'
  | 'glrlm'
  | 'glszm'
  | 'gldm'
  | 'ngtdm'

export type RadiomicsNormalization = 'none' | 'z-score' | 'auto'

export interface RadiomicsConfig {
  source_version?: 'legacy' | 'physical-v1'
  discretisation?: RadiomicsDiscretisation | null
  processing_order?: 'legacy' | 'interpolate_then_normalize'
  resegmentation?: RadiomicsResegmentation
  features?: { catalog_version: string; keys: string[] }
  feature_classes: RadiomicsFeatureClass[]
  resampling: [number, number, number] | 'none'
  image_interpolator?: 'sitkBSpline' | 'sitkLinear' | 'sitkNearestNeighbor'
  mask_interpolator?: 'sitkNearestNeighbor' | 'sitkLinear'
  mask_threshold?: number
  normalization: RadiomicsNormalization
  bin_width: number | null
  normalization_scale?: number
  include_liver_features?: boolean
}


export type RadiomicsDiscretisationRule =
  | { method: 'none' }
  | { method: 'fbs'; bin_width: number; bin_origin?: number | null }
  | { method: 'fbn'; bin_count: number }

export type RadiomicsResegmentation =
  | { method: 'none' }
  | {
    method: 'range'
    lower: number
    upper: number
    intensity_basis: { kind: 'calibrated' | 'arbitrary' | 'unknown'; unit: string; source?: string }
    ibsi_deviation_acknowledged?: boolean
  }

export interface RadiomicsDiscretisation {
  intensity_histogram: RadiomicsDiscretisationRule
  texture: RadiomicsDiscretisationRule
  intensity_volume_histogram: { method: 'auto' } | { method: 'fbs'; bin_width: number; bin_origin?: number | null }
}


export interface RadiomicsFeatureDefinition {
  section: string
  family: string
  name: string
  code: string
  key: string
  definition: string
  required_mask: string
  required_masks: ('morphological' | 'intensity')[]
  requires_discretisation: boolean
  requires_image_intensity: boolean
  requires_local_context: boolean
  supported_dimensions: ('2d' | '3d')[]
  discretisation_group: 'none' | 'intensity_histogram' | 'intensity_volume_histogram' | 'texture'
  calculation_module: 'morphology' | 'local' | 'statistics' | 'texture' | 'gldzm' | 'custom'
  legacy_pyradiomics_class: RadiomicsFeatureClass | null
  standard_status: 'computed' | 'not_standardized' | 'custom'
  status: 'computed' | 'not_standardized'
  source_note: string | null
  reason: string | null
  unit: string | null
  runtime_constraints: string[]
}
