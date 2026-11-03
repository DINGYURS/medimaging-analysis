"""
Radiomics Feature Extraction & Clinical Quantification Engine.
Wraps PyRadiomics (IBSI-compliant) to extract 3D shape, first-order intensity,
and texture features, translating them into clinically interpretable metrics.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import SimpleITK as sitk
from radiomics import featureextractor


# Suppress noisy PyRadiomics debug logs
logging.getLogger("radiomics").setLevel(logging.ERROR)


def get_default_radiomics_extractor(
    bin_width: float = 25.0,
    resampled_spacing: Optional[Tuple[float, float, float]] = (1.0, 1.0, 1.0)
) -> featureextractor.RadiomicsFeatureExtractor:
    """
    Construct a standardized IBSI-compliant PyRadiomics extractor.
    
    Default configuration:
        - binWidth = 25 HU (standard for CT imaging)
        - resampledPixelSpacing = [1.0, 1.0, 1.0] mm (isotropic resampling)
        - interpolator = sitkBSpline for image, sitkNearestNeighbor for mask
        - enabled feature classes: shape, firstorder, glcm, glrlm, glszm, gldm, ngtdm
    """
    settings: Dict[str, Any] = {
        "binWidth": bin_width,
        "correctMask": True,
        "label": 1,
    }

    if resampled_spacing:
        settings["resampledPixelSpacing"] = list(resampled_spacing)
        settings["interpolator"] = "sitkBSpline"

    # PyRadiomics schema requires settings to be enclosed in 'setting' dict
    params: Dict[str, Any] = {
        "setting": settings,
        "imageType": {
            "Original": {}
        },
        "featureClass": {
            "shape": None,
            "firstorder": None,
            "glcm": None,
            "glrlm": None,
            "glszm": None,
            "gldm": None,
            "ngtdm": None,
        }
    }

    extractor = featureextractor.RadiomicsFeatureExtractor(params)
    return extractor


def extract_lesion_radiomics(
    image: sitk.Image,
    mask: sitk.Image,
    lesion_id: int = 1,
    extractor: Optional[featureextractor.RadiomicsFeatureExtractor] = None,
) -> Dict[str, Any]:
    """
    Extract comprehensive quantitative radiomics from a 3D image and binary mask ROI.
    
    Returns structured dict containing:
        - raw_features: full pyradiomics output dict
        - morphology: key 3D geometric properties
        - intensity: HU density statistics
        - texture: heterogeneity and complexity metrics
        - clinical_summary: high-level summary for clinical assessment & LLM prompt input
    """
    if extractor is None:
        extractor = get_default_radiomics_extractor()

    # PyRadiomics calculation
    raw_result = extractor.execute(image, mask)

    # Filter out diagnostics metadata to keep only feature values
    features: Dict[str, float] = {}
    for k, v in raw_result.items():
        if k.startswith("original_"):
            try:
                features[k] = float(np.asarray(v).item())
            except Exception:
                pass

    # Group into clinical categories
    morphology = {
        "volume_mm3": round(features.get("original_shape_MeshVolume", features.get("original_shape_VoxelVolume", 0.0)), 2),
        "voxel_volume_mm3": round(features.get("original_shape_VoxelVolume", 0.0), 2),
        "surface_area_mm2": round(features.get("original_shape_SurfaceArea", 0.0), 2),
        "surface_to_volume_ratio": round(features.get("original_shape_SurfaceVolumeRatio", 0.0), 4),
        "sphericity": round(features.get("original_shape_Sphericity", 0.0), 4),
        "compactness_1": round(features.get("original_shape_Compactness1", 0.0), 4),
        "compactness_2": round(features.get("original_shape_Compactness2", 0.0), 4),
        "max_3d_diameter_mm": round(features.get("original_shape_Maximum3DDiameter", 0.0), 2),
        "major_axis_length_mm": round(features.get("original_shape_MajorAxisLength", 0.0), 2),
        "minor_axis_length_mm": round(features.get("original_shape_MinorAxisLength", 0.0), 2),
        "least_axis_length_mm": round(features.get("original_shape_LeastAxisLength", 0.0), 2),
        "elongation": round(features.get("original_shape_Elongation", 0.0), 4),
        "flatness": round(features.get("original_shape_Flatness", 0.0), 4),
    }

    intensity = {
        "mean_hu": round(features.get("original_firstorder_Mean", 0.0), 1),
        "median_hu": round(features.get("original_firstorder_Median", 0.0), 1),
        "std_hu": round(features.get("original_firstorder_StandardDeviation", 0.0), 1),
        "min_hu": round(features.get("original_firstorder_Minimum", 0.0), 1),
        "max_hu": round(features.get("original_firstorder_Maximum", 0.0), 1),
        "percentile_10_hu": round(features.get("original_firstorder_10Percentile", 0.0), 1),
        "percentile_90_hu": round(features.get("original_firstorder_90Percentile", 0.0), 1),
        "skewness": round(features.get("original_firstorder_Skewness", 0.0), 4),
        "kurtosis": round(features.get("original_firstorder_Kurtosis", 0.0), 4),
        "entropy": round(features.get("original_firstorder_Entropy", 0.0), 4),
        "uniformity": round(features.get("original_firstorder_Uniformity", 0.0), 4),
    }

    texture = {
        "glcm_contrast": round(features.get("original_glcm_Contrast", 0.0), 4),
        "glcm_correlation": round(features.get("original_glcm_Correlation", 0.0), 4),
        "glcm_joint_entropy": round(features.get("original_glcm_JointEntropy", 0.0), 4),
        "glcm_homogeneity": round(features.get("original_glcm_Idm", 0.0), 4),
        "glrlm_gray_level_non_uniformity": round(features.get("original_glrlm_GrayLevelNonUniformity", 0.0), 2),
        "glszm_zone_entropy": round(features.get("original_glszm_ZoneEntropy", 0.0), 4),
        "ngtdm_coarseness": round(features.get("original_ngtdm_Coarseness", 0.0), 6),
        "ngtdm_busyness": round(features.get("original_ngtdm_Busyness", 0.0), 4),
        "ngtdm_complexity": round(features.get("original_ngtdm_Complexity", 0.0), 2),
    }

    # Clinical Interpretation Logic
    mean_hu = intensity["mean_hu"]
    if mean_hu < -350:
        attenuation_type = "纯磨玻璃结节 (pGGO / Non-solid)"
    elif mean_hu <= -100:
        attenuation_type = "部分实性/混合磨玻璃结节 (Part-Solid / Subsolid)"
    elif mean_hu <= 150:
        attenuation_type = "实性结节 (Solid nodule)"
    else:
        attenuation_type = "高密度/钙化结节 (Calcified / Hyperdense)"

    sphericity = morphology["sphericity"]
    if sphericity >= 0.80:
        margin_shape_eval = "形态较规则，类圆形，边缘较光整"
    elif sphericity >= 0.65:
        margin_shape_eval = "形态轻度欠规则，可见分叶或轻度切迹"
    else:
        margin_shape_eval = "形态不规则，球形度偏低，需关注毛刺征或棘状突起"

    clinical_summary = {
        "lesion_id": lesion_id,
        "max_diameter_mm": morphology["max_3d_diameter_mm"],
        "major_axis_mm": morphology["major_axis_length_mm"],
        "volume_mm3": morphology["volume_mm3"],
        "mean_attenuation_hu": mean_hu,
        "attenuation_category": attenuation_type,
        "sphericity": sphericity,
        "margin_morphology_description": margin_shape_eval,
        "internal_heterogeneity_entropy": intensity["entropy"],
    }

    return {
        "lesion_id": lesion_id,
        "clinical_summary": clinical_summary,
        "morphology": morphology,
        "intensity": intensity,
        "texture": texture,
        "total_extracted_features_count": len(features),
        "all_raw_features": features,
    }


def export_radiomics_to_dataframe(
    radiomics_results: List[Dict[str, Any]]
) -> pd.DataFrame:
    """Convert list of radiomics result dicts into a single tabular pandas DataFrame."""
    records = []
    for item in radiomics_results:
        row: Dict[str, Any] = {"lesion_id": item["lesion_id"]}
        # Flatten clinical summary
        for k, v in item["clinical_summary"].items():
            if k != "lesion_id":
                row[f"clinical_{k}"] = v
        # Flatten morphology
        for k, v in item["morphology"].items():
            row[f"shape_{k}"] = v
        # Flatten intensity
        for k, v in item["intensity"].items():
            row[f"intensity_{k}"] = v
        # Flatten texture
        for k, v in item["texture"].items():
            row[f"texture_{k}"] = v
        records.append(row)
    return pd.DataFrame(records)
