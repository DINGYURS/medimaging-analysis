"""File-backed CT reference generation. Analysis tasks and reports are read-only."""
import asyncio
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory

import SimpleITK as sitk

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.services import ct_reference_attributes, ct_nodule_semantics
from app.services.ct_reference_evidence import EvidenceUnavailable, candidates, original_slices
from app.services.ct_reference_text import cloud_input, organize, render, SYSTEM_PROMPT
from app.services.insight_client import ProviderError
from app.services.insight_payload import signature, source_record

logger = logging.getLogger(__name__)
reference_gate = asyncio.Semaphore(1)
RUNNER = Path(__file__).with_name("ct_lobe_inference.py")
CHECKPOINT = (
    "Dataset291_TotalSegmentator_part1_organs_1559subj/"
    "nnUNetTrainerNoMirroring__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth"
)


@dataclass(frozen=True)
class ReferenceSource:
    task_id: int
    volume: str
    dicom_dir: str | None
    series_uid: str | None
    lesions: tuple

    @property
    def mask(self):
        return Path(settings.MASK_DIR) / f"task_{self.task_id}_mask.nii.gz"

    @property
    def result(self):
        return Path(settings.STORAGE_DIR) / "ct_references" / f"task_{self.task_id}.json"

    @property
    def source_file(self):
        return Path(f"{self.mask}.source.json")


def _file_stamp(path):
    try:
        return signature(path)
    except OSError:
        return None


def fingerprint(source):
    dicoms = []
    if source.dicom_dir and Path(source.dicom_dir).is_dir():
        dicoms = [_file_stamp(path) for path in sorted(Path(source.dicom_dir).iterdir()) if path.is_file()]
    files = [
        source.volume, source.mask, source.source_file, RUNNER,
        Path(__file__), Path(__file__).with_name("ct_reference_evidence.py"),
        Path(__file__).with_name("ct_reference_text.py"),
    ]
    checkpoint = Path(settings.CT_REFERENCE_LOBE_WEIGHTS) / CHECKPOINT if settings.CT_REFERENCE_LOBE_WEIGHTS else None
    inputs = [
        "ct-reference-v1", source.series_uid, source.lesions, dicoms,
        [_file_stamp(path) for path in files], _file_stamp(checkpoint) if checkpoint else None,
        settings.CT_REFERENCE_LOBE_PYTHON, settings.LLM_MODEL, settings.LLM_API_BASE_URL, SYSTEM_PROMPT,
    ]
    if settings.CT_REFERENCE_TEXTURE_PYTHON and settings.CT_REFERENCE_TEXTURE_WEIGHTS:
        inputs += [
            settings.CT_REFERENCE_TEXTURE_PYTHON,
            _file_stamp(settings.CT_REFERENCE_TEXTURE_WEIGHTS),
            _file_stamp(ct_nodule_semantics.TEXTURE_RUNNER),
            _file_stamp(Path(ct_nodule_semantics.__file__)),
            _file_stamp(Path(ct_reference_attributes.__file__)),
            ct_reference_attributes.SYSTEM_PROMPT,
        ]
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _completed(source, digest):
    try:
        saved = json.loads(source.result.read_text(encoding="utf-8"))
        if saved["fingerprint"] == digest:
            response = saved["response"]
            for item in response.get("result", {}).get("findings", []):
                if item["anchor"]:
                    item["anchor"] = {name: item["anchor"][name] for name in ("slice", "endpoints_xy")}
            return response
        return {"status": "STALE"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "NOT_GENERATED"}


def reserve(source):
    source.result.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = source.result.with_suffix(".lock").open("a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def status(source):
    path = source.result.with_suffix(".lock")
    if path.exists():
        with path.open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "RUNNING"}
    return _completed(source, fingerprint(source))


def _save(source, digest, response):
    with NamedTemporaryFile(dir=source.result.parent, prefix=source.result.stem + ".",
                            suffix=".tmp", mode="w", encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump({"fingerprint": digest, "response": response}, output,
                      ensure_ascii=False, allow_nan=False)
            output.flush()
            os.replace(temporary, source.result)
        finally:
            temporary.unlink(missing_ok=True)


def _load(source):
    try:
        actual = json.loads(source.source_file.read_text(encoding="utf-8"))
        expected = source_record(source.volume, str(source.mask))
    except (OSError, ValueError):
        raise EvidenceUnavailable("当前任务缺少可核对的分割来源。") from None
    if actual != expected:
        raise EvidenceUnavailable("当前任务的候选掩膜来源已变化。")
    image = sitk.ReadImage(source.volume)
    mask = sitk.ReadImage(str(source.mask))
    if image.GetDimension() != 3 or mask.GetDimension() != 3:
        raise EvidenceUnavailable("当前影像或掩膜缺少三维空间信息。")
    if not image.IsSameImageGeometryAs(mask, 1e-4, 1e-5):
        raise EvidenceUnavailable("候选掩膜与所选 CT 序列的物理空间不一致。")
    anchors, is_chest = original_slices(image, source.dicom_dir, source.series_uid)
    if not is_chest:
        raise EvidenceUnavailable("当前序列缺少可确认的胸部 CT 来源。")
    return image, mask, anchors


def _infer_lobes(source, directory):
    if not settings.CT_REFERENCE_LOBE_PYTHON or not settings.CT_REFERENCE_LOBE_WEIGHTS:
        return None
    weights = Path(settings.CT_REFERENCE_LOBE_WEIGHTS)
    if not (weights / CHECKPOINT).is_file():
        return None
    output = Path(directory) / "lobes.nii.gz"
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(settings.CT_REFERENCE_LOBE_GPU)}
    try:
        subprocess.run(
            [settings.CT_REFERENCE_LOBE_PYTHON, str(RUNNER), source.volume, str(output), str(weights)],
            cwd=directory, env=env, timeout=300, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return sitk.ReadImage(str(output))
    except (OSError, subprocess.SubprocessError, RuntimeError):
        logger.warning("Lung lobe inference unavailable for CT reference task %s", source.task_id)
        return None


async def _make_result(source):
    image, mask, anchors = await asyncio.to_thread(_load, source)
    lobes = None
    if source.lesions:
        await gpu_gate.acquire()
        try:
            with TemporaryDirectory(prefix="lobes-", dir=source.result.parent) as directory:
                lobes = await asyncio.to_thread(_infer_lobes, source, directory)
        finally:
            gpu_gate.release()
    findings, lobe_available = await asyncio.to_thread(candidates, image, mask, lobes, source.lesions, anchors)
    measured = [item for item in findings if item["diameter_raw_mm"] is not None]
    order, text_source = "distribution_first", "template"
    enriched = bool(settings.CT_REFERENCE_TEXTURE_PYTHON and settings.CT_REFERENCE_TEXTURE_WEIGHTS)
    plan = None
    if enriched:
        await asyncio.to_thread(ct_nodule_semantics.describe, source, findings)
    if measured and len(findings) <= settings.LLM_MAX_LESIONS and settings.LLM_MODEL == "deepseek-v4-flash":
        representative = max(measured, key=lambda item: item["diameter_raw_mm"])["id"]
        payload = (ct_reference_attributes.cloud_input(findings, representative)
                   if enriched else cloud_input(findings, representative))
        if len(json.dumps(payload, ensure_ascii=False).encode()) <= settings.LLM_MAX_INPUT_BYTES:
            try:
                if enriched:
                    plan = await asyncio.to_thread(ct_reference_attributes.organize, payload, settings)
                    order = plan["order"]
                else:
                    order = await asyncio.to_thread(organize, payload, settings)
                text_source = "deepseek-v4-flash"
            except ProviderError:
                logger.info("CT reference task %s used template text", source.task_id)
    overview, summary = render(findings, lobe_available, order)
    coverage = ["本次覆盖肺结节候选，其他胸部征象未评估。"]
    if findings and not lobe_available:
        coverage.append("本次肺叶定位未评估。")
    elif any(item["lobe"] is None for item in findings):
        coverage.append("部分候选的肺叶定位不确定。")
    if any(item["diameter_mm"] is None for item in findings):
        coverage.append("部分候选缺少可用的轴位长径。")
    if measured and not anchors:
        coverage.append("轴位径线来自体数据，本次无法核对原始切片链接。")
    extra = {}
    if enriched:
        representative = max(measured, key=lambda item: item["diameter_raw_mm"])["id"] if measured else None
        payload = ct_reference_attributes.cloud_input(findings, representative)
        extra = {
            "attribute_details": ct_reference_attributes.render_details(findings, plan, payload),
            "attribute_plan": plan,
        }
        density_states = {attr["status"] for item in findings for attr in item["attributes"]
                          if attr["code"] == "density_texture"}
        if "indeterminate" in density_states:
            coverage.append("有候选的密度分类随裁剪变化或超出模型适用范围，未给出确定分类。")
        if "not_assessed" in density_states:
            coverage.append("部分候选的密度分类未完成评估。")
        if "research_prediction" in density_states:
            coverage.append("密度分类为 Nodule-CADx 研究适配预测，单病例仅用于链路验证。")
        coverage.append("毛刺征、分叶征、钙化、空洞及胸膜牵拉未评估。")
    for item in findings:
        item.pop("diameter_raw_mm")
    return {"status": "READY", "result": {
        "overview": overview, "summary": summary, "findings": findings,
        "coverage": coverage, "text_source": text_source, **extra,
    }}


async def generate(source, lock):
    try:
        digest = await asyncio.to_thread(fingerprint, source)
        try:
            async with reference_gate:
                result = await _make_result(source)
        except EvidenceUnavailable as exc:
            result = {"status": "BLOCKED", "message": str(exc)}
        except Exception:
            logger.warning("CT reference generation failed for task %s", source.task_id)
            result = {"status": "FAILED", "message": "本次影像参考生成失败，可以重试。"}
        if digest == await asyncio.to_thread(fingerprint, source):
            await asyncio.to_thread(_save, source, digest, result)
    finally:
        lock.close()
