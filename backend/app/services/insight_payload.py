"""Whitelist cloud inputs. Never forward metadata, paths or heuristic clinical labels."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.models import AnalysisTask, Lesion
from src.dicom_io import verify_dicom_intensity_unit

FEATURES = [
    ("original_shape_Maximum3DDiameter", "最大三维直径", "mm", "measurements"),
    ("original_shape_MeshVolume", "网格体积", "mm3", "measurements"),
    ("original_shape_VoxelVolume", "体素体积", "mm3", "measurements"),
    ("original_shape_Sphericity", "球形度", "dimensionless", "radiomics"),
    ("original_firstorder_Mean", "平均强度", None, "measurements"),
    ("original_firstorder_Median", "中位强度", None, "measurements"),
    ("original_firstorder_Entropy", "灰度熵", "bit", "radiomics"),
    ("original_glcm_Contrast", "GLCM 对比度", "dimensionless", "radiomics"),
]
DEFINITIONS = {
 "original_shape_Maximum3DDiameter": "ROI表面两点间最大三维距离，不等于二维平均径",
 "original_shape_MeshVolume": "ROI表面网格围成的体积",
 "original_shape_VoxelVolume": "ROI体素数量乘以单体素物理体积",
 "original_shape_Sphericity": "三维ROI接近球体的程度，不能直接判断边缘征象",
 "original_firstorder_Mean": "ROI内体素强度的算术平均值，不能单独判定结节类型",
 "original_firstorder_Median": "ROI内体素强度的中位数",
 "original_firstorder_Entropy": "当前离散化协议下ROI灰度分布的熵，无通用临床阈值",
 "original_glcm_Contrast": "当前协议下共生灰度差异的加权度量，无通用临床阈值",
}
EXTRA_FEATURES = [
 ("original_shape_Elongation", "伸长度", "dimensionless", "radiomics", "ROI主轴尺度比例，描述伸长程度"),
 ("original_shape_Flatness", "扁平度", "dimensionless", "radiomics", "ROI主轴尺度比例，描述扁平程度"),
 ("original_firstorder_Minimum", "最低强度", None, "measurements", "ROI内最低体素强度"),
 ("original_firstorder_Maximum", "最高强度", None, "measurements", "ROI内最高体素强度"),
 ("original_firstorder_10Percentile", "强度第10百分位", None, "measurements", "ROI强度分布的第10百分位切点"),
 ("original_firstorder_90Percentile", "强度第90百分位", None, "measurements", "ROI强度分布的第90百分位切点"),
 ("original_glcm_JointEntropy", "GLCM联合熵", "bit", "radiomics", "当前协议下共生灰度分布的联合熵，无通用临床阈值"),
 ("original_glcm_Correlation", "GLCM相关性", "dimensionless", "radiomics", "当前协议下共生灰度的线性相关度量"),
]
FEATURES.extend(x[:4] for x in EXTRA_FEATURES)
DEFINITIONS.update({x[0]: x[4] for x in EXTRA_FEATURES})
PROCESSING_KEYS = ("resampling_mode", "custom_spacing", "bin_width", "interpolator",
                   "resegmentation_enabled", "resegment_min_hu", "resegment_max_hu", "selected_classes")

class InputUnavailable(ValueError):
    pass

def signature(path: str) -> list:
    p = Path(path)
    s = p.stat()
    return [str(p.resolve()), s.st_size, s.st_mtime_ns]

def source_record(image_path: str, mask_path: str) -> dict:
    return {"source": "current_case_inference", "image": signature(image_path), "mask": signature(mask_path)}

def make_payload(task, full_features: list, unit: dict, verified: bool) -> dict:
    study = task.study
    if study.modality != "CT":
        raise InputUnavailable("当前病灶分析来源仅适用于肺部 CT，不能生成其他模态的洞察。")
    if not verified:
        raise InputUnavailable("缺少当前检查的分割来源记录，请重新运行影像分析后生成洞察。")
    rows = {str(item.get("lesion_id")): item for item in full_features if isinstance(item, dict)}
    lesions, missing = [], []
    for lesion in sorted(task.lesions, key=lambda x: x.lesion_index):
        lid = f"L{lesion.lesion_index}"
        row = rows.get(str(lesion.lesion_index), {})
        raw = row.get("all_raw_features", {})
        if not isinstance(raw, dict):
            raw = {}
        item = {"lesion_id": lid, "anatomical_location": None, "observations": [],
                "measurements": [], "radiomics": []}
        for key, label, physical_unit, group in FEATURES:
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                missing.append(f"{lid}.{key}")
                continue
            if key.startswith("original_shape_") and value <= 0:
                missing.append(f"{lid}.{key}")
                continue
            if key.endswith("Sphericity") and value > 1:
                missing.append(f"{lid}.{key}")
                continue
            item[group].append({"evidence_id": f"{lid}:{key}", "name": key, "label": label,
                                "value": value, "unit": physical_unit or unit.get("unit"),
                                "source": "PyRadiomics", "definition": DEFINITIONS[key]})
        provenance = row.get("provenance", {})
        item["roi_definition"] = "single_candidate_3d"
        item["physician_review"] = "not_recorded"
        item["processing"] = {k: provenance[k] for k in PROCESSING_KEYS if k in provenance}
        # Only structured numeric settings from a new extraction are trusted; never infer defaults.
        protocol = row.get("computation_protocol")
        item["computation_protocol"] = None
        if isinstance(protocol, dict):
            # Rebuild a bounded whitelist; do not forward arbitrary stored strings or paths.
            settings = protocol.get("settings", {})
            safe_settings = {}
            if isinstance(settings, dict):
                for key in ("binWidth", "binCount", "resampledPixelSpacing", "resegmentRange",
                            "force2D", "force2Ddimension", "distances", "symmetricalGLCM",
                            "normalize", "normalizeScale", "voxelArrayShift", "label"):
                    value = settings.get(key)
                    values = value if isinstance(value, list) else [value]
                    if len(values) <= 10 and all(v is None or isinstance(v, (bool, int, float))
                            and (not isinstance(v, float) or math.isfinite(v)) for v in values):
                        safe_settings[key] = value
                for key, allowed in {
                    "interpolator": ("sitkBSpline", "sitkLinear", "sitkNearestNeighbor", 1, 2, 23),
                    "resegmentMode": ("absolute", "relative", "sigma"),
                    "weightingNorm": ("manhattan", "euclidean", "infinity", "no_weighting"),
                }.items():
                    value = settings.get(key)
                    if value is None or value in allowed:
                        safe_settings[key] = value
            version = str(protocol.get("software_version", ""))
            item["computation_protocol"] = {
                "software": "PyRadiomics",
                "software_version": version if __import__("re").fullmatch(r"v?[0-9][0-9a-zA-Z.+-]{0,40}", version) else "unknown",
                "dimensionality": protocol.get("dimensionality") if protocol.get("dimensionality") in ("2D", "3D") else "unknown",
                "image_types": [v for v in protocol.get("image_types", []) if v == "Original"],
                "settings": safe_settings,
                "ibsi_benchmark_status": "not_recorded",
            }
        if not protocol:
            missing.append(f"{lid}.computation_protocol")
        lesions.append(item)
    warnings = ["仅覆盖算法检出的候选病灶，不代表完整影像评价。", "未提供历史检查对照。"]
    if unit.get("unit") != "HU":
        warnings.append("强度单位未经原始 DICOM 与已保存体数据一致性确认，不能解释为 HU。")
    if not lesions:
        warnings.append("算法未返回候选病灶，不等于影像正常。")
    return {
        "schema_version": "clinical-input-v2",
        "exam": {"modality": study.modality, "body_region": "unknown",
                 "analysis_scope": "lung_ct_candidates", "purpose": "unknown"},
        "clinical_context": {"age_years": None, "smoking_history": "unknown",
                             "known_malignancy": "unknown", "symptoms": [],
                             "prior_exam_available": None},
        "findings": {"source": "automated_analysis", "coverage": "detected_candidates_only",
                     "candidate_count": len(lesions), "lesions": lesions},
        "quality": {"intensity_unit": unit.get("unit"), "segmentation_source_verified": True,
                    "physician_review": "not_recorded", "reference_population": None,
                    "ibsi_benchmark_status": "not_recorded",
                    "missing_fields": missing, "warnings": warnings + [
                        "自动分割尚无医生复核记录。",
                        "未提供适用参考群体或IBSI基准验证记录。"]},
        "references": [],
    }

async def load_task(db, task_id: int):
    query = select(AnalysisTask).options(
        selectinload(AnalysisTask.study),
        selectinload(AnalysisTask.lesions).selectinload(Lesion.radiomics),
    ).where(AnalysisTask.id == task_id)
    return (await db.execute(query)).scalar_one_or_none()

async def build_payload(task) -> dict:
    if task.status != "COMPLETED":
        raise InputUnavailable("影像分析尚未完成。")
    # The sidecar also covers zero-candidate tasks, for which no Lesion row exists.
    from app.core.config import settings
    mask_path = str(Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz")
    if len(task.lesions) > settings.LLM_MAX_LESIONS:
        raise InputUnavailable("候选病灶数量超过当前洞察输入上限，暂不自动发送。")
    def read():
        try:
            record = json.loads(Path(mask_path + ".source.json").read_text())
            verified = (record == source_record(task.study.nifti_path, mask_path))
        except (OSError, ValueError, TypeError):
            verified = False
        feature_path = Path(settings.RADIOMICS_DIR) / f"task_{task.id}_features.json"
        try:
            feature_bytes = feature_path.read_bytes()
            data = json.loads(feature_bytes.decode("utf-8"))
        except (OSError, ValueError):
            raise InputUnavailable("组学结果文件不可用。") from None
        if not isinstance(data, list):
            raise InputUnavailable("组学结果格式不正确。")
        metadata = task.study.metadata_json or {}
        unit = verify_dicom_intensity_unit(task.study.dicom_dir, task.study.nifti_path,
                                           metadata.get("series_instance_uid"))
        payload = make_payload(task, data, unit, verified)
        payload["quality"]["feature_content_sha256"] = hashlib.sha256(feature_bytes).hexdigest()
        if len(json.dumps(payload, ensure_ascii=False).encode()) > settings.LLM_MAX_INPUT_BYTES:
            raise InputUnavailable("本次洞察输入超过大小上限，暂不自动发送。")
        return payload
    return await asyncio.to_thread(read)

def payload_hash(payload: dict, prompt_hash: str, model: str, base_url: str) -> str:
    value = {"input": payload, "prompt": prompt_hash, "model": model, "base_url": base_url}
    # MySQL JSON may round a binary float by one ULP during persistence.
    # Canonicalize the fingerprint only; keep source/prompt measurements intact.
    def canonical(item):
        if isinstance(item, float):
            return float(format(item, ".12g"))
        if isinstance(item, dict):
            return {key: canonical(v) for key, v in item.items()}
        if isinstance(item, list):
            return [canonical(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()
