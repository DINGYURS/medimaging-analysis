"""Execute the migrated kernels on already verified, co-registered XYZ arrays.

No DB, file writes, task launching or implicit whole-mask fallback. Source reading
and confirmation are separate adapters and are not replaced by modality guesses.
"""
from __future__ import annotations
import numpy as np
import time
from dataclasses import replace
from .catalog import CUSTOM_FEATURE_KEYS, get_research_feature_catalog, radiomics_catalog_version
from .config import RadiomicsConfig
from .protocol import prepare_radiomics_volume, resolve_radiomics_protocol, RadiomicsProtocolError
from .ibsi_features import compute_ibsi_features, IBSI_ENGINE_VERSION
from .lung_nodule_custom_features import compute_lung_nodule_custom_features
from .resilience import dependency_plan, isolated_ibsi, isolated_custom

def calculate_prepared_source(image_xyz, mask_xyz, spacing_xyz, affine, config, *, modality, intensity_unit=None, intensity_verified=False, profile=None, liver_profile=None):
    started=time.perf_counter()
    cfg=RadiomicsConfig.model_validate(config)
    image=np.asarray(image_xyz);mask=np.asarray(mask_xyz)
    if image.ndim!=3 or image.shape!=mask.shape or not mask.any(): raise ValueError('需要同网格非空三维影像和单病灶ROI')
    if not np.isfinite(image).all() or not np.isfinite(mask).all(): raise ValueError('影像和ROI必须是有限数值')
    if not set(np.unique(mask)) <= {0,1}: raise ValueError('必须传入明确的单病灶二值ROI，不接受标签图或概率图')
    if np.count_nonzero(np.any(mask,axis=(0,1)))<2: raise ValueError('本次三维计算要求ROI至少覆盖两个切层')
    if cfg.resegmentation.method=='range' and cfg.resegmentation.intensity_basis.kind=='calibrated':
        if not intensity_verified or (intensity_unit or '').casefold()!=cfg.resegmentation.intensity_basis.unit.casefold(): raise ValueError('校准阈值单位缺少匹配的源文件证据')
    requirements=dependency_plan(cfg.features.keys)
    effective=cfg.model_dump()
    if not requirements["intensity"]:
        effective["normalization"]="none"
        effective["resegmentation"]={"method":"none"}
    if effective['bin_width'] is None: effective['bin_width']=5.0 if modality.upper() in {'MR','MRI'} else 25.0
    normalization_failure=None
    try:
        prepared=prepare_radiomics_volume(image,mask,spacing_xyz,affine,resolve_radiomics_protocol(effective,modality=modality),modality=modality,allow_empty_intensity_mask=True)
    except RadiomicsProtocolError as error:
        if str(error) not in {'image has no signal variation for normalization','image contains non-finite values after normalization'}:
            raise
        # Preserve the geometry branch. Intensity-dependent targets are not
        # evaluated on unnormalised substitutes when requested normalisation fails.
        normalization_failure=str(error)
        geometry_effective={**effective,'normalization':'none'}
        prepared=prepare_radiomics_volume(image,mask,spacing_xyz,affine,resolve_radiomics_protocol(geometry_effective,modality=modality),modality=modality,allow_empty_intensity_mask=True)
    keys=cfg.features.keys;ibsi_keys=[k for k in keys if k.startswith('ibsi_')];custom_keys=[k for k in keys if k in CUSTOM_FEATURE_KEYS]
    numerical_modality='CT' if intensity_verified and intensity_unit=='HU' and prepared.protocol.normalization=='none' else 'normalized_signal' if prepared.protocol.normalization=='z-score' else 'unknown_signal'
    # Preserve formulas; excessive dense allocations become explicit unavailable
    # outcomes, without subsampling or changing the requested binning.
    definitions={r['key']:r for r in get_research_feature_catalog()}
    blocked={k:'强度归一化依赖失败：'+normalization_failure for k in keys if normalization_failure and 'intensity' in definitions[k]['required_masks']}
    roi_values=prepared.image_xyz[prepared.intensity_mask_xyz]
    for group in ['intensity_histogram','texture','intensity_volume_histogram']:
        group_keys=[k for k in ibsi_keys if definitions[k]['discretisation_group']==group and k not in blocked]
        if not group_keys:continue
        if not roi_values.size:
            blocked.update({k:'强度掩膜为空；该离散化分支不可计算' for k in group_keys})
            continue
        rule=getattr(cfg.discretisation,group) if cfg.discretisation else {'method':'auto' if group=='intensity_volume_histogram' else 'fbs','bin_width':prepared.protocol.bin_width}
        if hasattr(rule,'model_dump'):rule=rule.model_dump()
        if rule['method']=='fbn':levels=rule['bin_count']
        else:
            width=(1.0 if numerical_modality=='CT' else prepared.protocol.bin_width) if rule['method']=='auto' else rule.get('bin_width',prepared.protocol.bin_width)
            origin=rule.get('bin_origin')
            if origin is None:origin=float(roi_values.min())
            levels=(float(roi_values.max())-origin)/width+1
        if not np.isfinite(float(levels)) or levels>4096 or group=='texture' and levels*roi_values.size>64_000_000:
            blocked.update({k:'离散化矩阵超出资源限制（最多4096级、预计6400万单元）；未抽样或改写分箱参数' for k in group_keys})
    allowed=[k for k in ibsi_keys if k not in blocked]
    ibsi_requirements=dependency_plan(allowed)
    ibsi=isolated_ibsi(compute_ibsi_features,prepared.image_xyz,prepared.mask_xyz,prepared.target_spacing_xyz,allowed,bin_width=prepared.protocol.bin_width,modality=numerical_modality,morphology_mask_xyz=prepared.mask_xyz if ibsi_requirements["morphology"] else np.zeros_like(prepared.mask_xyz),intensity_mask_xyz=prepared.intensity_mask_xyz if ibsi_requirements["intensity"] else np.zeros_like(prepared.intensity_mask_xyz),discretisation=cfg.discretisation.model_dump() if cfg.discretisation else None) if allowed else {'features':{},'unavailable':{},'provenance':{}}
    ibsi['unavailable'].update(blocked)
    values=dict(ibsi['features']);unavailable={k:v for k,v in ibsi['unavailable'].items() if k in keys}
    if custom_keys:
        if profile!='lung_ct' or numerical_modality!='CT': unavailable.update({k:'自定义增量仅适用于已核实HU且未归一化的肺CT' for k in custom_keys})
        else:
            custom,custom_errors=isolated_custom(compute_lung_nodule_custom_features,prepared.image_xyz,prepared.mask_xyz,prepared.target_spacing_xyz,custom_keys)
            unavailable.update(custom_errors)
            values.update({k:custom[k] for k in custom_keys if k in custom and np.isfinite(custom[k])})
            unavailable.update({k:'原公式在当前ROI未产生有限值' for k in custom_keys if k not in values and k not in unavailable})
    values={k:float(v) for k,v in values.items() if k in keys and np.isfinite(v)}
    for key in keys:
        if key not in values: unavailable.setdefault(key,'当前输入无法计算该特征')
    liver = None
    if cfg.include_liver_features:
        from .liver import supplement
        geometry_only = not requirements["intensity"] and not any(k in CUSTOM_FEATURE_KEYS for k in keys)
        liver_prepared = replace(prepared, image_xyz=np.zeros_like(prepared.image_xyz)) if geometry_only else prepared
        liver = supplement(liver_prepared, modality, liver_profile or profile, normalization_failure, intensity_unit if intensity_verified else None)
        liver['input_mode'] = 'geometry_only_zero_signal' if geometry_only else 'prepared_image_signal'
    return {'features':values,'unavailable':unavailable,'counts':{'selected':len(keys),'computed':len(values),'unavailable':len(unavailable)},'catalog_version':radiomics_catalog_version(),'engine_version':IBSI_ENGINE_VERSION,'config':cfg.model_dump(),'provenance':{'processing':prepared.provenance,'dependencies':requirements,'normalization_dependency_failure':normalization_failure,'ibsi':ibsi['provenance'],'intensity_unit':intensity_unit,'intensity_verified':intensity_verified,'liver_supplement':liver,'profile':profile,'liver_profile':liver_profile or profile,'kernel_seconds':time.perf_counter()-started}}
