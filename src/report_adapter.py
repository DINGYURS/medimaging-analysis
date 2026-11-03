"""
Report Adapter & LLM Prompt Template Formatter.
Structures imaging findings, spatial coordinates, and quantitative radiomics features
into clean, clinical-grade Prompt payloads for downstream Large Language Models (LLMs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def format_llm_clinical_insight_payload(
    dicom_metadata: Dict[str, Any],
    lesion_findings: List[Dict[str, Any]],
    radiomics_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Format complete analysis pipeline results into structured JSON payload
    and ready-to-use LLM clinical reasoning prompt.
    """
    patient_context = {
        "patient_id": dicom_metadata.get("patient_id", "Anonymous"),
        "modality": dicom_metadata.get("modality", "CT"),
        "study_description": dicom_metadata.get("study_description", "Chest CT"),
        "slice_count": dicom_metadata.get("slice_count", 0),
        "slice_thickness_mm": dicom_metadata.get("slice_thickness_mm", 0.0),
        "pixel_spacing_mm": dicom_metadata.get("pixel_spacing", []),
    }

    lesions_structured = []
    for lesion in radiomics_results:
        lid = lesion["lesion_id"]
        # Find corresponding spatial finding
        finding = next((f for f in lesion_findings if f.get("lesion_id") == lid), {})
        summary = lesion.get("clinical_summary", {})
        morph = lesion.get("morphology", {})
        intensity = lesion.get("intensity", {})
        texture = lesion.get("texture", {})

        lesion_data = {
            "lesion_id": lid,
            "spatial_location": {
                "world_lps_coords_mm": finding.get("centroid_world_lps_mm", []),
                "centroid_voxel": finding.get("centroid_voxel_xyz", []),
            },
            "clinical_measurements": {
                "longest_diameter_mm": summary.get("max_diameter_mm"),
                "major_axis_mm": summary.get("major_axis_mm"),
                "volume_mm3": summary.get("volume_mm3"),
                "equivalent_spherical_diameter_mm": finding.get("equivalent_diameter_mm"),
            },
            "density_and_attenuation": {
                "mean_hu": intensity.get("mean_hu"),
                "median_hu": intensity.get("median_hu"),
                "attenuation_type": summary.get("attenuation_category"),
                "hu_range": [intensity.get("min_hu"), intensity.get("max_hu")],
            },
            "morphological_features": {
                "sphericity": morph.get("sphericity"),
                "compactness": morph.get("compactness_1"),
                "surface_to_volume_ratio": morph.get("surface_to_volume_ratio"),
                "margin_evaluation": summary.get("margin_morphology_description"),
            },
            "internal_heterogeneity": {
                "entropy": intensity.get("entropy"),
                "glcm_contrast": texture.get("glcm_contrast"),
                "texture_complexity": texture.get("ngtdm_complexity"),
            }
        }
        lesions_structured.append(lesion_data)

    # Build standard structured prompt string
    prompt_lines = [
        "你是一名资深的放射科临床专家与胸部影像诊断医师。请根据以下患者胸部 CT 扫描的结构化量化分析数据（包括病灶解剖定位、体积测算、三维形态组学与密度分布特征），撰写一份专业的【影像学所见】、【影像学诊断意见】与【下一步临床随访建议】：",
        "",
        "### 患者与检查信息",
        f"- 检查类型：{patient_context['modality']} ({patient_context['study_description']})",
        f"- 检查层数与层厚：{patient_context['slice_count']} 层，层厚 {patient_context['slice_thickness_mm']} mm",
        "",
        f"### 算法识别病灶与定量组学指标（检出病灶共 {len(lesions_structured)} 处）",
    ]

    for item in lesions_structured:
        c = item["clinical_measurements"]
        d = item["density_and_attenuation"]
        m = item["morphological_features"]
        h = item["internal_heterogeneity"]
        loc = item["spatial_location"]

        prompt_lines.append(f"#### 病灶 #{item['lesion_id']}:")
        prompt_lines.append(f"- 三维解剖空间坐标 (LPS mm): {loc['world_lps_coords_mm']}")
        prompt_lines.append(f"- 尺寸与体积：最大长径 {c['longest_diameter_mm']} mm，长轴 {c['major_axis_mm']} mm，三维体积 {c['volume_mm3']} mm³")
        prompt_lines.append(f"- 密度特征：平均 CT 值 {d['mean_hu']} HU（区间 {d['hu_range'][0]} ~ {d['hu_range'][1]} HU），属于【{d['attenuation_type']}】")
        prompt_lines.append(f"- 形态特征：球形度 {m['sphericity']}，形态评估：{m['margin_evaluation']}")
        prompt_lines.append(f"- 内部异质性：灰度熵值 {h['entropy']}，纹理复杂度 {h['texture_complexity']}")
        prompt_lines.append("")

    prompt_lines.extend([
        "### 输出要求",
        "1. 【影像所见】：客观详述病灶的位置、形态、大小（长径/体积）、密度特点及边缘情况；",
        "2. 【影像诊断】：给出定性或鉴别诊断倾向（参考 Fleischner 指南或 Lung-RADS 分级倾向）；",
        "3. 【临床建议】：给出具体随访周期（如 3/6/12 个月复查薄层 CT）或进一步检查建议（增强扫描/PET-CT/穿刺活检等）。"
    ])

    formatted_prompt = "\n".join(prompt_lines)

    return {
        "status": "ready_for_llm_inference",
        "patient_context": patient_context,
        "lesion_count": len(lesions_structured),
        "lesions": lesions_structured,
        "llm_prompt_template": formatted_prompt
    }
