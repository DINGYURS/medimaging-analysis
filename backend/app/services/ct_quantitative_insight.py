"""Versioned CT evidence snapshots and the anonymous quantitative insight request."""
import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import ct_quantitative as q, ct_quantitative_radiomics as rad
from app.services import ct_verified_insight as specialist
from app.services.ct_reference_evidence import EvidenceUnavailable, candidate_components, original_slices
from app.services.feature_calculation.lung_nodule_custom_features import _otsu_threshold
from app.services.insight_client import ProviderError, SYSTEM_PROMPT as BASE_PROMPT, request_json, validate_output
from app.services.insight_payload import InputUnavailable, signature, source_record
from src.dicom_io import verify_dicom_intensity_unit

VERSION = "ct-quantitative-insight-v1"
SYSTEM_PROMPT = BASE_PROMPT + """
逐病灶覆盖要求：按 findings.lesions 的顺序，先为每个 measurements 或 radiomics 非空的候选生成一条单独的 quantitative_interpretation。
每条填写该候选的 lesion_id，写出该候选自身至少一个可用指标及其实际数值，并引用对应的同病灶 evidence_id。
优先选最容易核对的一至三项指标。全部有指标的候选各有一条后，再考虑给个别候选补充第二条。
提交前逐个核对。摘要和 review_items 中的提及不能替代单独的解读。两组指标都为空的候选不生成定量解读。
定量扩展：exam.measurements 与 findings.distribution 是检查级事实。摘要可提及这些事实。
findings.lesions[].anatomical_location 来自已配准肺叶掩膜的重叠。候选计数仅描述本次算法输出。
HU 直方图用于了解分布，只有带 evidence_id 的测量可以引用具体百分比、径线和强度。
阈上体积分数与轴位阈上长径比是本项目图像计算量。不要据此判定密度分型。肺分割外缘距离只描述与掩膜边界的距离。
逐项指标可用来解释测量，勿把直方图、梯度、轮廓或组学特征描述成肉眼确认的征象。只选择少数指标归纳。
数值沿用证据单位，体积可写 mm³ 或 mL，表面积可写 mm²，百分比用 %，请勿换算单位。
"""
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
REGION_KINDS = ("lobes", "pleura", "vessels", "airways")
GEOMETRY = (
    ("diameter_3d_mm", "三维最大径", "mm", "0.5 等值面三角网格顶点间最长距离"),
    ("voxel_volume_mm3", "候选体素体积", "mm3", "候选 ROI 体素数乘物理体素体积"),
    ("surface_mm2", "候选表面积", "mm2", "候选 ROI 三角网格总面积"),
    ("sphericity", "球形度", "dimensionless", "同体积球体表面积除以候选网格表面积"),
    ("elongation", "伸长度", "dimensionless", "物理体素中心协方差中、最大特征值之比的平方根"),
    ("flatness", "扁平度", "dimensionless", "物理体素中心协方差最小、最大特征值之比的平方根"),
    ("convex_hull_volume_deficit", "三维凸包体积缺额比例", "dimensionless", "1 减去候选网格与三维凸包体积之比"),
)
HU_FIELDS = (
    ("mean_hu", "平均 HU"), ("median_hu", "中位 HU"), ("min_hu", "最低 HU"), ("max_hu", "最高 HU"),
    ("p05_hu", "P5 HU"), ("p10_hu", "P10 HU"), ("p25_hu", "P25 HU"), ("p75_hu", "P75 HU"),
    ("p90_hu", "P90 HU"), ("p95_hu", "P95 HU"), ("iqr_hu", "HU 四分位距"), ("std_hu", "HU 标准差"),
)
THRESHOLD_FIELDS = (
    ("threshold_hu", "阈上成分分界值", "HU", "候选内 Otsu 分界；无法获得 Otsu 时使用 -150 HU"),
    ("volume_mm3", "阈上成分体积", "mm3", "候选中 HU 大于或等于分界值的体素总体积"),
    ("volume_fraction_percent", "阈上体积分数（3D CTR）", "%", "阈上体素体积除以候选体素体积"),
    ("max_component_diameter_3d_mm", "阈上成分最大三维径", "mm", "各阈上 6 连通域网格最大径的最大值"),
    ("max_component_axial_diameter_mm", "阈上成分最大轴位径", "mm", "各阈上 6 连通域同层体素中心轴位长径的最大值"),
    ("axial_diameter_ratio_percent", "轴位阈上长径比", "%", "阈上成分最大轴位径除以候选最大轴位径，两径所在层可能不同"),
)
EDGE_FIELDS = (
    ("core_minus_shell_hu", "核心与边缘平均 HU 差", "HU", "距 ROI 外最近体素中心超过 1 mm 的核心均值减去边缘均值"),
    ("boundary_gradient_mean_hu_per_mm", "边界梯度幅度均值", "HU/mm", "边界体素强度中心差分的物理梯度幅度均值"),
    ("boundary_gradient_p90_hu_per_mm", "边界梯度幅度 P90", "HU/mm", "边界体素梯度幅度的第 90 百分位数"),
)


def region_inputs(study_id, volume, uid):
    directory = Path(settings.CT_QUANTITATIVE_MASK_DIR or Path(settings.STORAGE_DIR) / "ct_quantitative_masks")
    base = directory / f"study_{study_id}"
    manifest_path = base.with_suffix(".json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["ct"] != signature(volume) or manifest["series_uid"] != uid:
            return {}
        inputs = {}
        for key in REGION_KINDS:
            if key not in manifest["masks"]:
                continue
            supplied = manifest["masks"][key]
            filename = directory / f"{base.name}_{key}.nii.gz"
            try:
                record = signature(filename)
            except OSError:
                continue
            if supplied.get("signature") == record and supplied.get("source") in ("algorithm", "manual"):
                inputs[key] = {"signature": record, "source": supplied["source"]}
                for field in ("producer", "checkpoint_sha256"):
                    value = supplied.get(field)
                    if isinstance(value, str) and 0 < len(value) <= 120:
                        inputs[key][field] = value
        return inputs
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def snapshot(task, history):
    study = task.study
    uid = (study.metadata_json or {}).get("series_instance_uid")
    if (task.status != "COMPLETED" or not study.nifti_path or not study.dicom_dir or not uid
            or study.doctor_id != task.doctor_id or len(task.lesions) > settings.LLM_MAX_LESIONS):
        raise InputUnavailable("CT 检查、原片或候选病灶资料不足。")
    mask = str(Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz")
    try:
        source = source_record(study.nifti_path, mask)
        if json.loads(Path(mask + ".source.json").read_text()) != source:
            raise ValueError("changed segmentation")
        files = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(study.dicom_dir, uid)
        if not files:
            raise ValueError("missing DICOM series")
        dicoms = [signature(file) for file in files]
    except (OSError, ValueError, RuntimeError, TypeError):
        raise InputUnavailable("缺少与当前 CT 序列匹配的候选掩膜和原始 DICOM 来源。") from None
    try:
        models = specialist.snapshot(task)
    except InputUnavailable:
        models = None
    return {
        "schema_version": VERSION, "study_id": study.id, "doctor_id": task.doctor_id, "series_uid": uid,
        "volume": study.nifti_path, "mask": mask, "dicom_dir": study.dicom_dir, "dicom_files": dicoms,
        "segmentation_source": source, "regions": region_inputs(study.id, study.nifti_path, uid),
        "lesions": sorted([x.lesion_index, x.center_x, x.center_y, x.center_z] for x in task.lesions),
        "history": history, "models": models, "feature_catalog": rad.CATALOG_VERSION,
        "pipeline": [signature(path) for path in (Path(__file__), q.__file__, rad.__file__)],
    }


async def build_snapshot(task, db):
    if task.status != "COMPLETED":
        raise InputUnavailable("影像分析尚未完成。")
    return await asyncio.to_thread(snapshot, task, await rad.runs_for(db, task))


def anatomy(source, image):
    # Keep SimpleITK image owners alive for the duration of the NumPy views.
    images, arrays = {}, {}
    for kind, entry in source["regions"].items():
        try:
            segmentation = sitk.ReadImage(entry["signature"][0])
            if not image.IsSameImageGeometryAs(segmentation, 1e-4, 1e-5):
                continue
            values = sitk.GetArrayViewFromImage(segmentation)
            if not np.issubdtype(values.dtype, np.integer):
                continue
            if kind == "lobes":
                # Allow an incomplete segmentation to be detected instead of emitting partial organ volumes.
                if set(np.unique(values)) != {0, *q.LOBE_LABELS}:
                    continue
            elif values.min() < 0 or values.max() > 1 or not values.any():
                continue
            images[kind], arrays[kind] = segmentation, values
        except (OSError, RuntimeError):
            continue
    return images, arrays


def measure(source, history):
    image, mask = (sitk.ReadImage(source[name]) for name in ("volume", "mask"))
    anchors, is_chest = original_slices(image, source["dicom_dir"], source["series_uid"])
    if not is_chest or not anchors:
        raise EvidenceUnavailable("当前任务缺少与 CT 体数据逐层匹配的原始胸部 CT 序列。")
    volume = sitk.GetArrayViewFromImage(image)
    region_images, regions = anatomy(source, image)
    axes = q.plane_axes(image)
    spacing = np.asarray(image.GetSpacing()[::-1])
    vox_volume = float(np.prod(spacing))
    verified = (verify_dicom_intensity_unit(source["dicom_dir"], source["volume"], source["series_uid"])
                .get("unit") == "HU")
    digests = rad.source_digests(source) if history else None
    lesions, centers = [], {}
    for saved, bounds, roi in candidate_components(image, mask, source["lesions"]):
        index, *world = saved
        name = f"L{index}"
        centers[name] = world
        geometry = q.lesion_geometry(roi, bounds, spacing, axes, volume.shape)
        item = {"id": name, "geometry": geometry, "unavailable": {}}
        missing = item["unavailable"]
        for plane in q.PLANES:
            if not geometry["diameters"].get(plane):
                missing[f"{plane}_diameter"] = ("体数据网格未与该解剖平面对齐" if plane not in axes
                                                else "同层 ROI 少于两个体素中心")
        values = volume[bounds][roi]
        hu = q.hu_summary(values) if verified else None
        if hu:
            item["density"] = hu
            threshold = _otsu_threshold(values)
            item["solid"] = q.solid_features(volume, bounds, roi, spacing, axes.get("axial"),
                                              -150.0 if threshold is None else float(threshold),
                                              geometry["diameters"].get("axial"))
            item["solid"]["threshold_method"] = "fallback_constant" if threshold is None else "otsu_256_bins"
            if not item["solid"]["voxels"]:
                missing["solid_diameters"] = "当前阈值下掩膜内无阈上体素"
            item["edges"], edge_reason = q.edge_features(volume, bounds, roi, spacing)
            if edge_reason:
                missing["edge_or_core"] = edge_reason
        else:
            missing["hu"] = "未确认原始 CT 的 HU 或 ROI 含非有限像素"
        if "lobes" in regions:
            item["location"] = q.lobe_fractions(regions["lobes"], bounds, roi)
            if not item["location"]["lobe"]:
                missing["lobe_assignment"] = "单肺叶重叠比例未达到 80%"
        else:
            missing["lung_lobes"] = "没有同序列已登记并通过物理空间检查的五肺叶掩膜"
        item["region_distances"] = {}
        for key, mask_name in (("outer_lung", "lobes"), ("pleura", "pleura"),
                               ("vessels", "vessels"), ("airways", "airways")):
            if mask_name not in regions:
                missing[key] = "没有同序列已登记并通过物理空间检查的结构掩膜"
                continue
            distance = q.region_distance(regions[mask_name], bounds, roi, spacing, key)
            item["region_distances"][key] = distance
            if distance["distance_mm"] is None:
                missing[key] = "限定搜索范围内未取得结构掩膜距离"
        item["radiomics"], item["matched_histories"] = rad.feature_analysis(
            history, source, image, volume, roi, bounds, index, bool(hu), digests)
        lesions.append(item)
    return {
        "protocol": {**q.PROTOCOL, "spacing_xyz_mm": list(image.GetSpacing()), "dicom_slice_count": len(anchors),
                     "binary_connectivity": 6, "threshold_operator": ">=", "otsu_bins": 256,
                     "otsu_fallback_hu": -150.0,
                     "region_distances": "closest_native_voxel_centers_within_search_radius"},
        "hu_verified": verified, "lesions": lesions, "organs": q.organ_metrics(regions, volume, vox_volume, verified),
        "distribution": q.distribution(lesions, centers),
        "regions": {key: {field: value for field, value in source["regions"][key].items()
                          if field != "signature"} for key in region_images},
        "feature_definitions": list(rad.CATALOG.values()),
    }


def evidence(owner, key, label, value, unit, definition):
    return {"evidence_id": f"{owner}:{key}", "name": key, "label": label, "value": value,
            "unit": unit, "definition": definition}


def select_evidence(item):
    owner = item["id"]
    output = []

    def add(values, fields, prefix=""):
        for key, label, unit, definition in fields:
            value = values.get(key)
            if value is not None:
                output.append(evidence(owner, prefix + key, label, value, unit, definition))

    geometry = item["geometry"]
    if not geometry["touches_volume_boundary"]:
        add(geometry, GEOMETRY)
        for name, label in zip(q.PLANES, ("矢状位", "冠状位", "轴位")):
            measure = geometry["diameters"].get(name)
            if measure:
                output.append(evidence(owner, f"{name}_diameter", f"{label}最大径", measure["raw_mm"], "mm",
                                       "该平面同层 ROI 最远两个体素中心的物理距离"))
        add(geometry.get("axial_contours", {}), (
            ("max_area_mm2", "最大轴位轮廓面积", "mm2", "轴位每层 ROI 对应的物理面积最大值"),
            ("largest_adjacent_area_change_mm2", "相邻轴位层最大面积变化", "mm2", "连续轴位层 ROI 面积差绝对值的最大值"),
            ("mean_convex_hull_deficit", "轴位平均凸包面积缺额比例", "dimensionless", "逐层 1 减 ROI 面积与凸包面积之比的平均值"),
        ))
        add(item.get("solid", {}), THRESHOLD_FIELDS, "solid_")
    if item.get("density"):
        fields = [(key, label, "HU", "本项目对候选 ROI 原生 HU 体素的统计量") for key, label in HU_FIELDS]
        fields.append(("fixed_bin_entropy_bit", "固定分箱 HU 熵", "bit", "候选 HU 固定区间频率的 Shannon 熵"))
        add(item["density"], fields)
    add(item.get("edges", {}), EDGE_FIELDS)
    for region, label in (("outer_lung", "肺分割外缘"), ("pleura", "胸膜标注掩膜"),
                          ("vessels", "血管掩膜"), ("airways", "支气管掩膜")):
        add(item["region_distances"].get(region, {}), (
            ("distance_mm", f"至{label}最短距离", "mm",
             f"候选与{label}的最近体素中心距离；受分割质量和扫描覆盖影响"),
        ), region + "_")
    return output


def exam_evidence(audit):
    lungs = audit["organs"].get("lungs", {})
    facts = []
    for key, label in (("volume_ml", "双肺掩膜总体积"), ("left_volume_ml", "左肺掩膜体积"),
                       ("right_volume_ml", "右肺掩膜体积"), ("left_fraction_percent", "左肺体积占比")):
        if key in lungs:
            unit = "%" if key.endswith("percent") else "mL"
            facts.append(evidence("exam", key, label, lungs[key], unit, "由五叶掩膜体素计数计算"))
    for index, (label, value) in enumerate(lungs.get("lobe_volumes_ml", {}).items(), 1):
        facts.append(evidence("exam", f"lobe_{index}_ml", f"{label}掩膜体积", value, "mL", "该肺叶掩膜体素数乘体素体积"))
    for index, row in enumerate(audit["distribution"]["center_distances"], 1):
        if index > 10:
            break
        facts.append(evidence("exam", f"distance_{index}", "与".join(row["lesions"]) + "候选中心距离",
                              row["center_distance_mm"], "mm", "两个候选物理质心之间的欧氏距离"))
    if lungs.get("whole_lung_mask_hu"):
        facts.append(evidence("exam", "lung_median", "全肺掩膜中位 HU", lungs["whole_lung_mask_hu"]["median_hu"],
                              "HU", "五叶掩膜内全部体素的中位 HU"))
    return facts


def cloud_payload(audit):
    warnings = ["覆盖范围为算法检出的胸部 CT 候选。", "候选分割及测量待阅片核对。"]
    if not audit["hu_verified"]:
        warnings.append("原始 CT 强度未能确认为 HU。")
    if not audit["lesions"]:
        warnings.append("本次算法未提供候选掩膜。")
    if not audit["organs"]:
        warnings.append("缺少合格的五肺叶掩膜，无法计算肺叶归属和肺容积。")
    else:
        warnings.append("肺分割外缘距离依赖掩膜边界，无法据此确认胸膜受累。")
    if "pleura" not in audit["regions"]:
        warnings.append("缺少独立的胸膜标注掩膜，无法计算至标注胸膜的距离。")
    if any(key not in audit["regions"] for key in ("vessels", "airways")):
        warnings.append("血管或支气管空间关系缺少可用掩膜。")
    if any(row["geometry"]["touches_volume_boundary"] for row in audit["lesions"]):
        warnings.append("部分候选掩膜触及扫描边缘，几何测量可能受截断影响。")
    warnings.append("组学结果依赖各自的 ROI、重采样和离散化协议。")
    findings = []
    for item in audit["lesions"]:
        radiomics = []
        for key in ("ibsi_TU9B", "ibsi_ACUI", "ibsi_N365", "custom_edge_sharpness_acutance_3d"):
            row = item["radiomics"]
            value = row["features"].get(key)
            if value is None or not audit["hu_verified"]:
                continue
            definition = rad.CATALOG[key]
            radiomics.append(evidence(item["id"], key, definition["name"], value,
                                       "bit" if key == "ibsi_TU9B" else "dimensionless", definition["definition"]))
        density = item.get("density")
        processing = item["radiomics"]["actual_protocol"].get("processing", {})
        bin_width = processing.get("bin_width")
        findings.append({
            "lesion_id": item["id"], "anatomical_location": (item.get("location") or {}).get("lobe"),
            "measurements": select_evidence(item), "radiomics": radiomics,
            "radiomics_family_counts": item["radiomics"]["groups"],
            "radiomics_protocol": {"native_grid": processing.get("resampled") is False,
                                   "bin_width": bin_width if type(bin_width) in (int, float) else None},
            "hu_histogram_fractions": density["bin_fractions"] if density else None,
        })
    return {
        "schema_version": VERSION,
        "exam": {"modality": "CT", "analysis_scope": "lung_ct_candidates", "measurements": exam_evidence(audit)},
        "clinical_context": {"prior_exam_available": None},
        "findings": {"lesions": findings,
                     "distribution": {key: audit["distribution"][key] for key in
                                      ("candidate_count", "counts_by_lobe", "counts_by_side")}},
        "quality": {"warnings": warnings, "missing_fields": any(x["unavailable"] for x in audit["lesions"]),
                    "intensity_unit": "HU" if audit["hu_verified"] else None,
                    "hu_histogram_bin_edges": list(q.HU_BOUNDS) if audit["hu_verified"] else [],
                    "spacing_xyz_mm": audit["protocol"]["spacing_xyz_mm"]},
        "references": [],
    }


def fit_cloud_budget(payload):
    def size():
        return len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    if size() <= settings.LLM_MAX_INPUT_BYTES:
        return
    # Preserve at least one citable measurement or radiomics feature per measurable candidate.
    payload["quality"]["warnings"].append("本次云端输入使用关键指标子集，完整测量保存在计算记录。")
    lesions = payload["findings"]["lesions"]
    # All three fields can be large when the exam contains many candidates.
    base_keys = {"diameter_3d_mm", "axial_diameter", "voxel_volume_mm3", "mean_hu", "solid_volume_fraction_percent"}
    for lesion in lesions:
        for key in ("radiomics_family_counts", "radiomics_protocol", "hu_histogram_fractions"):
            lesion.pop(key)
        measurements = lesion["measurements"]
        lesion["measurements"] = [m for m in measurements if m["name"] in base_keys] or measurements[:1]
        lesion["radiomics"] = [] if lesion["measurements"] else lesion["radiomics"][:1]
    if size() > settings.LLM_MAX_INPUT_BYTES:
        raise ProviderError("本次候选数量和关键测量超出云端输入大小上限。")


async def generate(source):
    async with AsyncSessionLocal() as db:
        history = await rad.load_history(db, source["history"])
    try:
        audit = await asyncio.to_thread(measure, source, history)
    except EvidenceUnavailable as exc:
        raise ProviderError(str(exc)) from None
    payload = cloud_payload(audit)
    fit_cloud_budget(payload)
    evidence_rows = [*payload["exam"]["measurements"], *[
        fact for item in payload["findings"]["lesions"] for group in ("measurements", "radiomics") for fact in item[group]
    ]]
    model_audit = None
    if evidence_rows:
        result, usage = await asyncio.to_thread(request_json, payload, settings, SYSTEM_PROMPT, validate_output)
    else:
        result = {"schema_version": "clinical-insight-v2", "analysis_status": "insufficient",
                  "result_summary": "本次算法未提供可供测量的肺部候选掩膜。", "quantitative_interpretation": [],
                  "review_items": [], "limitations": payload["quality"]["warnings"]}
        usage = {}
    if source["models"] and audit["hu_verified"] and audit["lesions"]:
        try:
            model_result, model_usage, model_audit = await specialist.generate(source["models"])
            usage = {key: usage.get(key, 0) + model_usage.get(key, 0) for key in usage.keys() | model_usage.keys()}
            evidence_rows.extend(model_audit["evidence"])
            result["quantitative_interpretation"].extend(model_result["quantitative_interpretation"])
        except ProviderError:
            result["limitations"].append("专项模型结果暂不可用，本次保留候选定量测量。")
    elif audit["lesions"] and not source["models"]:
        result["limitations"].append("当前专项模型资料不可用，本次仅提供候选定量测量。")
    return result, usage, {"evidence": evidence_rows, "cloud_input": payload,
                           "measurements": audit, "specialist": model_audit}
