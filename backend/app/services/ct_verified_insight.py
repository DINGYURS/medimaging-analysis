"""Only independently validated local CT findings may enter the cloud plan."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.services.insight_client import ProviderError, request_json
from app.services.insight_payload import InputUnavailable, signature, source_record
from src.dicom_io import verify_dicom_intensity_unit


VERSION = "ct-verified-attributes-v1"
MODEL_ROOT = Path(settings.STORAGE_DIR) / "ct_verified_models"
RUNTIME = Path(settings.BASE_DIR) / ".tmp/ct-ai-reference/runtime/bin/python"
RUNNER = Path(__file__).with_name("ct_verified_runner.py")
SYSTEM_PROMPT = (
    '你负责组织已验证的CT模型证据。输入只有匿名病灶编号、证据编号和模型标签。'
    '输出单个JSON对象，仅含 order 数组，按你认为最易读的顺序列出全部 evidence_id。'
    '同一病灶的证据必须相邻，每条恰好出现一次。不要生成任何其他字段或描述。'
)
LABELS = {
    ("density", "solid"): ("密度分类", "实性", "density-soft-tta"),
    ("density", "non_solid"): ("密度分类", "非实性", "density-soft-tta"),
    ("calcification", "present"): ("钙化识别", "有钙化", "cnn-v3"),
    ("calcification", "absent"): ("钙化识别", "未检出钙化", "cnn-v3"),
    ("spiculation", "localized"): ("毛刺定位", "毛刺候选区域", "shape-refine"),
}


def snapshot(task):
    study = task.study
    volume = study.nifti_path
    mask = str(Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz")
    uid = (study.metadata_json or {}).get("series_instance_uid")
    lesions = sorted((x.lesion_index, float(x.center_x), float(x.center_y), float(x.center_z))
                     for x in task.lesions)
    if not volume or not study.dicom_dir or not uid or len(lesions) > settings.LLM_MAX_LESIONS:
        raise InputUnavailable("当前 CT 缺少可用的原片或候选病灶资料。")
    try:
        provenance = source_record(volume, mask)
        if json.loads(Path(mask + ".source.json").read_text()) != provenance:
            raise ValueError("mask provenance differs")
        scripts = sorted(MODEL_ROOT.glob("helpers/*.py"))
        weights = sorted(p for p in MODEL_ROOT.glob("*/*") if p.suffix in (".json", ".pt", ".joblib"))
        if len(scripts) != 6 or len(weights) != 6:
            raise ValueError("model assets missing")
        pipeline = [signature(p) for p in [RUNNER, Path(__file__), *scripts, *weights]]
    except (OSError, ValueError, TypeError):
        raise InputUnavailable("专项模型或 CT 分割来源尚未就绪。") from None
    return {"schema_version": VERSION, "volume": volume, "mask": mask, "dicom_dir": study.dicom_dir,
            "series_uid": uid, "lesions": [list(x) for x in lesions], "source": provenance, "pipeline": pipeline}


def validate_order(content, payload):
    expected = {item["evidence_id"]: item["lesion_id"] for item in payload["evidence"]}
    plan = json.loads(content)
    if not isinstance(plan, dict) or set(plan) != {"order"} or not isinstance(plan["order"], list):
        raise ValueError("Unexpected evidence ordering schema")
    order = plan["order"]
    if len(order) != len(expected) or any(not isinstance(key, str) for key in order) or set(order) != set(expected):
        raise ValueError("Evidence order must be a permutation")
    groups = [expected[key] for key in order]
    if len(set(groups)) != sum(a != b for a, b in zip([None, *groups], groups)):
        raise ValueError("Evidence for a lesion is interleaved")
    return plan


def infer(payload):
    unit = verify_dicom_intensity_unit(payload["dicom_dir"], payload["volume"], payload["series_uid"])
    if unit.get("unit") != "HU":
        raise ProviderError("原始 CT 的 HU 来源无法核对。")
    with TemporaryDirectory(prefix="ct-verified-") as stage:
        source, result = (Path(stage) / name for name in ("input.json", "output.json"))
        source.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        try:
            run = subprocess.run([str(RUNTIME), str(RUNNER), str(MODEL_ROOT), str(source), str(result)],
                                 env={**os.environ, "CUDA_VISIBLE_DEVICES": str(settings.CT_REFERENCE_LOBE_GPU),
                                      "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                                      "PYTHONPATH": f"{settings.BASE_DIR}/backend:{settings.BASE_DIR}"},
                                 capture_output=True, text=True, timeout=220)
            if run.returncode:
                import logging
                logging.getLogger(__name__).error("CT attribute inference failed: %s", run.stderr[-2400:])
                raise ValueError("Model command failed")
            return json.loads(result.read_text())
        except (ValueError, OSError, subprocess.SubprocessError):
            raise ProviderError("专项影像识别暂不可用，原洞察已保留。", True) from None


def organize(raw):
    facts = {}
    for item in raw:
        label, text, model = LABELS[item["kind"], item["value"]]
        key = item["lesion_id"] + ":" + item["kind"]
        facts[key] = {"evidence_id": key, "lesion_id": item["lesion_id"], "label": label, "value": text,
                      "unit": None, "model": model, **({"anchor": item["anchor"]} if "anchor" in item else {})}
    cloud_input = {"evidence": [{"evidence_id": key, "lesion_id": info["lesion_id"], "label": info["label"],
                                "value": info["value"]} for key, info in facts.items()]}
    plan, usage = request_json(cloud_input, settings, SYSTEM_PROMPT, validate_order) if facts else ({"order": []}, {})
    groups = {}
    for key in plan["order"]:
        info = facts[key]
        groups.setdefault(info["lesion_id"], []).append(info)
    rows = []
    for lesion, evidence in groups.items():
        phrases = []
        for item in evidence:
            text = item["value"]
            if "anchor" in item:
                text += f"（原始 CT 第 {item['anchor']['slice']} 层）"
            phrases.append(text)
        rows.append({"lesion_id": lesion, "text": "，".join(phrases) + "。",
                     "evidence_ids": [item["evidence_id"] for item in evidence]})
    result = {"schema_version": VERSION, "analysis_status": "limited" if rows else "insufficient",
              "result_summary": "以下为通过门限的候选病灶模型结果。" if rows else "本次没有达到展示门限的专项结果。",
              "quantitative_interpretation": rows, "review_items": [], "limitations": []}
    audit = {"evidence": list(facts.values()), "cloud_input": cloud_input, "cloud_output": plan}
    return result, usage, audit


async def generate(payload):
    await gpu_gate.acquire()
    try:
        raw = await asyncio.to_thread(infer, payload)
    finally:
        gpu_gate.release()
    return await asyncio.to_thread(organize, raw)


PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
