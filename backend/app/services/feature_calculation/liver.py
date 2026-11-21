"""Adapters for unchanged source liver formulas and current study metadata."""
from types import SimpleNamespace
from .radiomics_profiles import resolve_radiomics_profile
from .liver_radiomics_features import compute_liver_radiomics_features, _DEFINITIONS, VERSION

def study_profile(study, source=None):
    metadata=dict(study.get("metadata_json") or {})
    for key,value in ((source or {}).get("anatomy") or {}).items():
        if not metadata.get(key):metadata[key]=value
    return resolve_radiomics_profile(SimpleNamespace(modality=study.get("modality"),description=study.get("study_description"),body_part=metadata.get("body_part") or metadata.get("BodyPartExamined"),series_metadata=metadata))

def supplement(prepared, modality, profile, normalization_failure=None, intensity_unit=None):
    if not str(profile).startswith("liver"):
        return {"status":"not_applicable","reason":"当前检查缺少适用的肝脏CT/MR解剖信息，未追加肝脏特征","features":{},"curves":{},"unavailable":{},"version":VERSION}
    context={"profile":profile,"intensity_unit":"normalized (z-score x scale)" if prepared.protocol.normalization=="z-score" else intensity_unit or ("a.u." if str(modality).upper() in {"MR","MRI"} else "unknown"),"processing":prepared.provenance}
    try:
        if normalization_failure:raise ValueError("请求的归一化失败："+normalization_failure)
        result=compute_liver_radiomics_features(prepared.image_xyz,prepared.mask_xyz,prepared.target_spacing_xyz,modality=modality)
        return {**result,**context,"status":"partial" if result["unavailable"] else "computed"}
    except Exception as error:
        return {**context,"status":"failed","features":{},"curves":{},"definitions":_DEFINITIONS,"unavailable":{k:"肝脏补充计算失败："+str(error)[:250] for k in _DEFINITIONS},"version":VERSION}
