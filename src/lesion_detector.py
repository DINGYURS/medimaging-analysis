"""
Lesion Detection & Segmentation Module for Medical Imaging Analysis Platform.
Integrates with pre-trained 3D deep learning models (nnU-Net 3d_fullres) on A100
via resident candidate detector service or local nnUNetv2_predict sub-process,
and provides connected-component post-processing to isolate individual lesion ROIs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nibabel as nib
import numpy as np
from scipy import ndimage as ndi
import SimpleITK as sitk


DEFAULT_NNUNET_RESULTS = Path("/data/medicine_image_mask/lung_nodule_candidate_detector/nnUNet_results")
DEFAULT_MODEL_DIR = DEFAULT_NNUNET_RESULTS / "Dataset712_LungLesionDoctor80Binary" / "nnUNetTrainer_250epochs__nnUNetResEncUNetLPlans_torchres_bs6__3d_fullres"
DEFAULT_PREDICT_BIN = Path("/data/miniconda3/envs/medsam2/bin/nnUNetv2_predict")

DEFAULT_CANDIDATE_ENDPOINTS = [
    "http://127.0.0.1:8112",
    "http://127.0.0.1:8113",
    "http://127.0.0.1:8114",
    "http://127.0.0.1:8115",
]


def _call_resident_candidate_service(
    input_nii_path: Path,
    endpoints: List[str] | None = None,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Call resident lung nodule candidate detector service running on A100."""
    candidate_endpoints = endpoints or []
    env_endpoints = os.environ.get("LUNG_NODULE_CANDIDATE_ENDPOINT")
    if env_endpoints:
        candidate_endpoints = [ep.strip() for ep in env_endpoints.split(",") if ep.strip()]
    if not candidate_endpoints:
        candidate_endpoints = DEFAULT_CANDIDATE_ENDPOINTS

    last_error = None
    for endpoint in candidate_endpoints:
        url = f"{endpoint.rstrip('/')}/predict_path"
        try:
            data = urllib.parse.urlencode({"image_path": str(input_nii_path.resolve())}).encode("utf-8")
            req = urllib.request.Request(
                url=url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            print(f"[*] Calling resident candidate detector service at {url}...")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    result = json.loads(resp.read().decode("utf-8"))
                    print(f"[+] Successfully received candidate prediction from {endpoint} (run_id: {result.get('run_id')})")
                    return result
        except Exception as e:
            print(f"[!] Endpoint {endpoint} failed or unreachable: {e}")
            last_error = e
            continue

    raise RuntimeError(f"All resident candidate endpoints failed. Last error: {last_error}")


def run_nnunet_inference(
    input_nii_path: str | Path,
    output_dir: str | Path,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    predict_bin: str | Path = DEFAULT_PREDICT_BIN,
    cuda_device: str = "2",
    min_volume_mm3: float = 30.0,
) -> Tuple[Path, List[Dict[str, Any]]]:
    """
    Run 3D nnU-Net inference on a single 3D NIfTI volume.
    Prefers the resident candidate detector service trained on Dataset712 on server,
    with fallback to local subprocess inference if needed.
    """
    input_nii_path = Path(input_nii_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_output_mask = output_dir / "sample_case.nii.gz"

    # 1. 优先尝试调用服务器常驻的 Dataset712 深度学习候选检测服务
    try:
        service_result = _call_resident_candidate_service(input_nii_path)
        generated_mask_path = Path(service_result.get("mask_path") or service_result.get("raw_argmax_mask_path"))
        if not generated_mask_path.exists():
            raise FileNotFoundError(f"Service returned mask path not found: {generated_mask_path}")

        # 将生成的真实模型掩膜复制到业务期望的输出目录
        shutil.copyfile(generated_mask_path, expected_output_mask)
        print(f"[+] Loaded AI mask from resident service: {expected_output_mask}")

        # 执行连通域后处理与形态学参数计算
        lesion_findings = analyze_lesion_components(
            mask_path=expected_output_mask,
            min_volume_mm3=min_volume_mm3,
        )
        return expected_output_mask, lesion_findings

    except Exception as service_err:
        print(f"[!] Resident candidate service call failed: {service_err}. Falling back to subprocess nnUNet...")

    # 2. 降级备用逻辑：本地子进程调用 nnUNetv2_predict
    temp_in_dir = output_dir / "temp_input"
    temp_in_dir.mkdir(parents=True, exist_ok=True)
    case_id = "sample_case"
    formatted_input = temp_in_dir / f"{case_id}_0000.nii.gz"
    if not formatted_input.exists():
        shutil.copyfile(input_nii_path, formatted_input)

    env = os.environ.copy()
    root_detector = Path("/data/medicine_image_mask/lung_nodule_candidate_detector")
    env["nnUNet_raw"] = str(root_detector / "nnUNet_raw")
    env["nnUNet_preprocessed"] = str(root_detector / "nnUNet_preprocessed")
    env["nnUNet_results"] = str(root_detector / "nnUNet_results")
    env["CUDA_VISIBLE_DEVICES"] = str(cuda_device)

    cmd = [
        str(predict_bin),
        "-i", str(temp_in_dir),
        "-o", str(output_dir),
        "-d", "712",
        "-c", "3d_fullres",
        "-f", "0",
        "-tr", "nnUNetTrainer_250epochs",
        "-p", "nnUNetResEncUNetLPlans_torchres_bs6",
        "-device", "cuda",
        "--disable_progress_bar",
    ]

    print(f"[*] Running fallback nnU-Net subprocess on GPU {cuda_device}...")
    result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"[!] nnU-Net prediction error stderr:\n{result.stderr}")
        raise RuntimeError(f"nnU-Net prediction failed with exit code {result.returncode}: {result.stderr}")

    if not expected_output_mask.exists():
        raise FileNotFoundError(f"Expected nnU-Net output mask not found at {expected_output_mask}")

    shutil.rmtree(temp_in_dir, ignore_errors=True)

    lesion_findings = analyze_lesion_components(
        mask_path=expected_output_mask,
        min_volume_mm3=min_volume_mm3,
    )
    return expected_output_mask, lesion_findings


def analyze_lesion_components(
    mask_path: str | Path,
    min_volume_mm3: float = 30.0,
) -> List[Dict[str, Any]]:
    """
    Analyze connected components in a binary/multi-label mask, filter small noise,
    and compute centroid, bounding box, and volume for each distinct lesion ROI.
    """
    mask_img = sitk.ReadImage(str(mask_path))
    mask_arr = sitk.GetArrayFromImage(mask_img)  # Shape (Z, Y, X)
    spacing = mask_img.GetSpacing()  # (sx, sy, sz) in mm
    voxel_volume_mm3 = spacing[0] * spacing[1] * spacing[2]

    # Label connected components
    labeled_arr, num_features = ndi.label(mask_arr > 0)
    findings: List[Dict[str, Any]] = []

    for label_idx in range(1, num_features + 1):
        component_mask = (labeled_arr == label_idx)
        voxel_count = int(np.sum(component_mask))
        volume_mm3 = float(voxel_count * voxel_volume_mm3)

        if volume_mm3 < min_volume_mm3:
            continue

        # Get bounding box in voxel indices (Z, Y, X)
        z_indices, y_indices, x_indices = np.where(component_mask)
        z_min, z_max = int(z_indices.min()), int(z_indices.max())
        y_min, y_max = int(y_indices.min()), int(y_indices.max())
        x_min, x_max = int(x_indices.min()), int(x_indices.max())

        # Centroid in voxel indices
        centroid_z = float(np.mean(z_indices))
        centroid_y = float(np.mean(y_indices))
        centroid_x = float(np.mean(x_indices))

        # Convert centroid to physical world coordinates (LPS mm)
        world_pt = mask_img.TransformContinuousIndexToPhysicalPoint([centroid_x, centroid_y, centroid_z])

        # Approximate equivalent spherical diameter (ESD = 2 * (3V / 4pi)^(1/3))
        equiv_diameter_mm = float(2.0 * (3.0 * volume_mm3 / (4.0 * np.pi)) ** (1.0 / 3.0))

        findings.append({
            "lesion_id": len(findings) + 1,
            "label_index": int(label_idx),
            "voxel_count": voxel_count,
            "volume_mm3": round(volume_mm3, 2),
            "equivalent_diameter_mm": round(equiv_diameter_mm, 2),
            "centroid_voxel_xyz": [round(centroid_x, 1), round(centroid_y, 1), round(centroid_z, 1)],
            "centroid_world_lps_mm": [round(world_pt[0], 2), round(world_pt[1], 2), round(world_pt[2], 2)],
            "bbox_voxel_xyz": {
                "x_range": [x_min, x_max],
                "y_range": [y_min, y_max],
                "z_range": [z_min, z_max],
            }
        })

    # Sort findings by volume descending
    findings.sort(key=lambda x: x["volume_mm3"], reverse=True)
    for idx, item in enumerate(findings):
        item["lesion_id"] = idx + 1

    return findings