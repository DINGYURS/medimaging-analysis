"""Assign supported, provenance-tagged attributes from local image recognition."""
import json
import logging
import math
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from app.core.config import settings
from src.dicom_io import verify_dicom_intensity_unit

logger = logging.getLogger(__name__)
TEXTURE_RUNNER = Path(__file__).with_name("ct_nodule_texture_inference.py")
TEXTURE_MODEL = "Nodule-CADx:dd0b3d1"
TEXTURE_LABELS = {
    "non_solid": "非实性",
    "non_to_part_solid": "非实性与部分实性之间",
    "part_solid": "部分实性",
    "part_to_solid": "部分实性与实性之间",
    "solid": "实性",
}
UNASSESSED = ("spiculation", "lobulation", "calcification", "cavitation", "pleural_retraction")


def texture_predictions(source):
    unit = verify_dicom_intensity_unit(source.dicom_dir, source.volume, source.series_uid)
    if unit.get("unit") != "HU":
        return {}
    targets = [{"id": f"L{value[0]}", "center_lps": value[1:]} for value in source.lesions]
    with TemporaryDirectory(prefix="attributes-", dir=source.result.parent) as directory:
        coordinates = Path(directory) / "candidates.json"
        coordinates.write_text(json.dumps(targets), encoding="utf-8")
        answer = subprocess.run(
            [settings.CT_REFERENCE_TEXTURE_PYTHON, str(TEXTURE_RUNNER), source.volume, str(source.mask),
             settings.CT_REFERENCE_TEXTURE_WEIGHTS, str(coordinates)],
            capture_output=True, text=True, check=True, timeout=150,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2"},
        )
    result = json.loads(answer.stdout)
    if result["model"] != TEXTURE_MODEL or set(result["predictions"]) != {item["id"] for item in targets}:
        raise ValueError("Unexpected texture result")
    return result["predictions"]


def describe(source, findings):
    try:
        predictions = texture_predictions(source) if findings else {}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        logger.warning("Texture model unavailable for CT reference task %s", source.task_id)
        predictions = {}
    for finding in findings:
        fid = finding["id"]
        prediction = predictions.get(fid, {})
        texture = prediction.get("class")
        density = {"id": f"{fid}:density_texture", "code": "density_texture", "status": "indeterminate" if prediction else "not_assessed"}
        if texture in TEXTURE_LABELS and prediction.get("feature_count") == 1688:
            density.update(status="research_prediction", value=texture, model=TEXTURE_MODEL,
                           protocol=prediction["protocol"])
        measurements = []
        for key, label in (("mean_hu", "平均强度"), ("median_hu", "中位强度")):
            value = prediction.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                measurements.append({"id": f"{fid}:{key}", "code": key, "status": "measured",
                                     "label": label, "value": value, "unit": "HU"})
        finding["attributes"] = measurements + [density] + [
            {"id": f"{fid}:{code}", "code": code, "status": "not_assessed"} for code in UNASSESSED
        ]


def attribute_text(finding, attribute_ids):
    reported = {x["id"]: x for x in finding.get("attributes", [])}
    clauses = []
    for ref in attribute_ids:
        item = reported[ref]
        if item["status"] == "measured":
            clauses.append(f"掩膜内{item['label']}为 {item['value']:.1f} HU。")
        elif item["status"] == "research_prediction":
            clauses.append(f"Nodule-CADx 原始 HU 研究适配预测的密度类型为{TEXTURE_LABELS[item['value']]}。")
    return "".join(clauses)
