"""Run the liver model on one native MRI sequence and preserve its grid."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import uuid

import httpx
import nibabel as nib
import numpy as np
from scipy import ndimage

from app.core.config import settings
from app.services.liver_mri_edge_refine import refine_liver_mri_mask_with_probabilities
from app.services.liver_mri_postprocess import postprocess_liver_mri_mask
from app.services.liver_mri_series import ROLE_CHANNEL

CHANNELS = ["t2", "pre", "ap", "pvp", "delay"]


def analysis_route(modality, metadata):
    modality = str(modality or "").strip().upper()
    if modality == "CT":
        return "lung_ct"
    if modality in {"MR", "MRI"} and metadata.get("import_mode") == "per_series_native":
        role = metadata.get("sequence_role")
        if role in ROLE_CHANNEL and ROLE_CHANNEL[role] == metadata.get("nnunet_channel"):
            if settings.LIVER_MRI_ENDPOINT:
                return "liver_mri"
            raise ValueError("肝脏 MRI 分割服务未配置")
    raise ValueError("当前没有适用于此检查的自动分割模型")


def _checked_mask(path, native):
    image = nib.load(str(path))
    if image.shape != native.shape or not np.allclose(image.affine, native.affine, rtol=1e-5, atol=1e-4):
        raise ValueError("肝脏 MRI 掩膜与输入影像网格不一致")
    labels = np.asanyarray(image.dataobj)
    if not np.isin(labels, [0, 1]).all():
        raise ValueError("肝脏 MRI 模型返回了非二值掩膜")
    return labels


def _model_output(path):
    result = Path(path).resolve(strict=True)
    if not result.is_relative_to(Path(settings.LIVER_MRI_RUN_ROOT).resolve()) or not result.is_file():
        raise ValueError("肝脏 MRI 模型输出路径不在已配置的推理目录")
    return result


def _findings(mask, affine):
    labels, count = ndimage.label(mask > 0)
    voxel_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
    if not np.isfinite(voxel_mm3) or voxel_mm3 <= 0:
        raise ValueError("肝脏 MRI 掩膜缺少有效物理空间")
    findings = []
    boxes = ndimage.find_objects(labels)
    for lesion_index in range(1, count + 1):
        box = boxes[lesion_index - 1]
        component = labels[box] == lesion_index
        centroid = np.array(ndimage.center_of_mass(component)) + [axis.start for axis in box]
        ras = affine @ np.r_[centroid, 1.0]
        volume = int(component.sum()) * voxel_mm3
        findings.append({
            "lesion_id": lesion_index,
            "volume_mm3": volume,
            "equivalent_diameter_mm": float((6 * volume / np.pi) ** (1 / 3)),
            "centroid_world_lps_mm": (ras[:3] * [-1, -1, 1]).tolist(),
        })
    return findings


def detect_liver_mri(volume_path: str, mask_path: str, metadata: dict):
    image_path = Path(volume_path).resolve(strict=True)
    destination = Path(mask_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    native = nib.load(str(image_path))
    if len(native.shape) != 3 or native.shape[2] < 2:
        raise ValueError("肝脏 MRI 推理需要至少两层的三维影像")
    channel = ROLE_CHANNEL[metadata["sequence_role"]]
    with TemporaryDirectory(prefix="liver_mri_", dir=destination.parent) as temp:
        work = Path(temp)
        zero = work / "zero.nii.gz"
        header = native.header.copy()
        header.set_data_dtype(np.float32)
        nib.save(nib.Nifti1Image(np.zeros(native.shape, dtype=np.float32), native.affine, header), zero)
        channel_paths = [str(zero)] * len(CHANNELS)
        channel_paths[channel] = str(image_path)

        with httpx.Client(timeout=httpx.Timeout(1800.0, connect=10.0), trust_env=False) as client:
            response = client.post(settings.LIVER_MRI_ENDPOINT.rstrip("/") + "/predict_paths", json={
                "case_id": "medimaging_analysis_" + uuid.uuid4().hex,
                "channel_paths": channel_paths,
            })
            if not response.is_success:
                raise RuntimeError(f"肝脏 MRI 分割服务请求失败（HTTP {response.status_code}）")
            prediction = response.json()
        model = prediction.get("model") or {}
        if (
            prediction.get("success") is not True
            or prediction.get("channel_order") != CHANNELS
            or model.get("dataset_name") != "Dataset570_LiverMRI_A125_Nested"
            or model.get("checkpoint_sha256") != settings.LIVER_MRI_CHECKPOINT_SHA256
        ):
            raise ValueError("肝脏 MRI 分割服务返回的通道或模型身份不匹配")

        raw = work / "raw.nii.gz"
        shutil.copyfile(_model_output(prediction["mask_path"]), raw)
        raw_voxels = int(np.count_nonzero(_checked_mask(raw, native)))
        current = raw
        probability = prediction.get("probability_path")
        edge = {"applied": False, "reason": "probabilities_unavailable"}
        if probability:
            probability = str(_model_output(probability))
            current, edge = refine_liver_mri_mask_with_probabilities(
                mask_nifti_path=current, probability_npz_path=probability, channel_paths=channel_paths,
            )
            _checked_mask(current, native)
        final, cleanup = postprocess_liver_mri_mask(
            mask_nifti_path=current, probability_npz_path=probability, output_path=work / "cleanup.nii.gz",
        )
        mask = _checked_mask(final, native)
        findings = _findings(mask, native.affine)
        shutil.copyfile(final, destination)

    destination.with_suffix(".gz.mri.json").write_text(json.dumps({
        "model": model,
        "run_id": prediction["run_id"],
        "input_mode": "per_series_native",
        "sequence_role": metadata["sequence_role"],
        "channel": channel,
        "raw_mask_voxels": raw_voxels,
        "mask_voxels": int(np.count_nonzero(mask)),
        "component_count": len(findings),
        "edge_refinement": edge,
        "postprocessing": cleanup,
    }, default=str), encoding="utf-8")
    return findings
