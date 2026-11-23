"""Reuse source-matched feature calculations and audit actual computation settings."""
import hashlib
import math
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.db.models import AnalysisTask
from app.services.ct_quantitative import expanded_bounds
from app.services.feature_calculation.catalog import get_research_feature_catalog, radiomics_catalog_version
from app.services.feature_calculation.engine import calculate_prepared_source
from app.services.feature_calculation.ibsi_features import LOCAL_PEAK_RADIUS_MM
from app.services.feature_calculation.inputs import digest_file
from app.services.feature_calculation.models import CalculationResult, CalculationRun

CATALOG = {item["key"]: item for item in get_research_feature_catalog()}
CATALOG_VERSION = radiomics_catalog_version()
DEFAULT_CONFIG = {
    "features": {"keys": sorted(CATALOG), "catalog_version": CATALOG_VERSION},
    "resampling": "none", "normalization": "none", "processing_order": "interpolate_then_normalize",
    "bin_width": 25.0, "resegmentation": {"method": "none"}, "source_version": "legacy",
}


async def runs_for(db, task):
    query = (select(CalculationRun.id, CalculationRun.input_hash)
             .join(AnalysisTask, AnalysisTask.id == CalculationRun.analysis_task_id)
             .where(AnalysisTask.study_id == task.study_id, AnalysisTask.doctor_id == task.doctor_id,
                    CalculationRun.doctor_id == task.doctor_id, CalculationRun.catalog_version == CATALOG_VERSION,
                    CalculationRun.status.in_(("succeeded", "partial")))
             .order_by((CalculationRun.analysis_task_id == task.id).desc(),
                       CalculationRun.finished_at.desc(), CalculationRun.id.desc()).limit(12))
    return [list(row) for row in (await db.execute(query)).all()]


async def load_history(db, run_keys):
    if not run_keys:
        return []
    runs = (await db.execute(select(CalculationRun).where(
        CalculationRun.id.in_([row[0] for row in run_keys])))).scalars().all()
    results = (await db.execute(select(CalculationResult).where(
        CalculationResult.run_id.in_([r.id for r in runs])))).scalars().all()
    grouped = {r.id: {"run": r, "rows": {}} for r in runs}
    for result in results:
        grouped[result.run_id]["rows"][result.lesion_index] = result
    return [grouped[key] for key, digest in run_keys
            if key in grouped and grouped[key]["run"].input_hash == digest]


def source_digests(source):
    # Match exact bytes of the original DICOM stack and saved CT, without binding to old task paths.
    entries = [digest_file(item[0]) for item in source["dicom_files"]]
    return {row["path"]: row["sha256"] for row in entries}, digest_file(source["volume"])["sha256"], \
        digest_file(source["mask"])["sha256"]


def matching_history(history, source, shape, roi, bounds, lesion_index, hu_verified, digests):
    if not history or digests is None:
        return []
    dicom, image_hash, mask_hash = digests
    full = np.zeros(shape[::-1], dtype=np.bool_)
    full[tuple(reversed(bounds))] = roi.T
    roi_hash = hashlib.sha256(full.tobytes()).hexdigest()
    matched = []
    for candidate in history:
        run, row = candidate["run"], candidate["rows"].get(lesion_index)
        if row is None or row.status not in ("computed", "partial"):
            continue
        snapshot = run.input_snapshot
        resource = snapshot.get("resource", {})
        study = resource.get("study", {})
        if (resource.get("doctor_id") != source["doctor_id"] or study.get("id") != source["study_id"]
                or study.get("metadata_json", {}).get("series_instance_uid") != source["series_uid"]):
            continue
        owned_roi = next((item for item in resource.get("lesions", [])
                          if item.get("lesion_index") == lesion_index and item.get("id") == row.lesion_id), None)
        roi_record = next((item for item in snapshot.get("mapping", [])
                           if item.get("lesion_index") == lesion_index and item.get("lesion_id") == row.lesion_id), None)
        if (owned_roi is None or roi_record is None or roi_record.get("mask_digest") != roi_hash
                or roi_record.get("voxels") != int(roi.sum()) or not owned_roi.get("mask_nifti_path")
                or not study.get("nifti_path")):
            continue
        recorded = {item["path"]: item["sha256"] for item in snapshot.get("files", [])}
        image_path = str(Path(study["nifti_path"]).resolve())
        mask_path = str(Path(owned_roi["mask_nifti_path"]).resolve())
        if recorded.get(image_path) != image_hash or recorded.get(mask_path) != mask_hash:
            continue
        if any(recorded.get(path) != sha for path, sha in dicom.items()) or not dicom:
            continue
        if hu_verified != (row.provenance.get("intensity_unit") == "HU"
                           and row.provenance.get("intensity_verified") is True):
            continue
        matched.append(candidate)
    return matched


def finite_values(raw):
    return {key: float(value) for key, value in raw.items() if key in CATALOG
            and type(value) in (int, float) and math.isfinite(value)}


def feature_report(raw, unavailable, provenance, config, origin, run=None):
    values = finite_values(raw)
    selected = set(config.get("features", {}).get("keys", []))
    missing = {key: unavailable.get(key, "本协议未产生有限值") if key in selected else "本协议未选择该特征"
               for key in CATALOG.keys() - values.keys()}
    groups = {}
    for key, definition in CATALOG.items():
        group = groups.setdefault(definition["family"], {"computed": 0, "unavailable": 0, "not_selected": 0})
        state = "computed" if key in values else "unavailable" if key in selected else "not_selected"
        group[state] += 1
    return {"origin": origin, "run": run, "computed_count": len(values),
            "features": values, "unavailable": dict(sorted(missing.items())), "groups": groups,
            "config": config, "actual_protocol": provenance}


def feature_analysis(history, source, image, volume, roi, bounds, index, hu_verified, digests):
    matched = matching_history(history, source, volume.shape, roi, bounds, index, hu_verified, digests)
    # Reuse only the same explicitly requested protocol. Other valid histories are preserved separately.
    matching_protocol = []
    histories = []
    for item in matched:
        run, row = item["run"], item["rows"][index]
        report = feature_report(row.features, row.unavailable, row.provenance, run.config, "persisted_history",
                                {"id": run.id, "task_id": run.analysis_task_id, "engine": run.engine_version})
        histories.append(report)
        processing = row.provenance.get("processing", {})
        ibsi = row.provenance.get("ibsi", {})
        if (set(run.config.get("features", {}).get("keys", [])) == CATALOG.keys()
                and run.config.get("source_version") == "legacy"
                and run.config.get("resegmentation") == {"method": "none"}
                and processing.get("resampled") is False and processing.get("bin_width") == 25
                and processing.get("normalization", {}).get("method") == "none"
                and ibsi.get("binning") == "fixed_bin_size"):
            matching_protocol.append(report)
    if matching_protocol:
        best = max(matching_protocol, key=lambda item: item["computed_count"])
        if best["computed_count"] == len(CATALOG):
            return best, [item for item in histories if item is not best]

    spacing = np.asarray(image.GetSpacing())
    context = np.ceil(LOCAL_PEAK_RADIUS_MM / spacing[::-1]) + 2
    crop = expanded_bounds(bounds, volume.shape, context)
    roi_crop = np.zeros(tuple(s.stop - s.start for s in crop), np.uint8)
    region = tuple(slice(a.start - b.start, a.stop - b.start) for a, b in zip(bounds, crop))
    roi_crop[region] = roi
    direction = np.asarray(image.GetDirection()).reshape(3, 3)
    lps_to_ras = np.diag([-1, -1, 1])
    affine = np.eye(4)
    affine[:3, :3] = lps_to_ras @ direction @ np.diag(spacing)
    affine[:3, 3] = lps_to_ras @ image.TransformIndexToPhysicalPoint(tuple(s.start for s in crop[::-1]))
    try:
        result = calculate_prepared_source(volume[crop].T, roi_crop.T, tuple(spacing), affine,
                                           DEFAULT_CONFIG, modality="CT", profile="lung_ct",
                                           intensity_unit="HU" if hu_verified else None,
                                           intensity_verified=hu_verified)
        protocol = {**result["provenance"], "native_crop_start_xyz": [s.start for s in crop[::-1]],
                    "original_shape_xyz": list(image.GetSize()), "engine_version": result["engine_version"]}
        return feature_report(result["features"], result["unavailable"], protocol, result["config"],
                              "native_grid_calculation"), histories
    except ValueError as exc:
        reason = f"本次 ROI 无法执行三维组学计算：{exc}"
        return feature_report({}, dict.fromkeys(CATALOG, reason), {}, DEFAULT_CONFIG,
                              "not_computed"), histories
