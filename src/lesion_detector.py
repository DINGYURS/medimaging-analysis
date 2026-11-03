"""
Lesion Detection & Segmentation Module for Medical Imaging Analysis Platform.
Integrates with pre-trained 3D deep learning models (nnU-Net 3d_fullres) on A100
and provides connected-component post-processing to isolate individual lesion ROIs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nibabel as nib
import numpy as np
from scipy import ndimage as ndi
import SimpleITK as sitk


DEFAULT_NNUNET_RESULTS = Path("/data/medicine_image_mask/lung_nodule_candidate_detector/nnUNet_results")
DEFAULT_MODEL_DIR = DEFAULT_NNUNET_RESULTS / "Dataset712_LungLesionDoctor80Binary" / "nnUNetTrainer_250epochs__nnUNetResEncUNetLPlans_torchres_bs6__3d_fullres"
DEFAULT_PREDICT_BIN = Path("/data/miniconda3/envs/medsam2/bin/nnUNetv2_predict")


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
    
    Args:
        input_nii_path: Path to input 3D NIfTI file.
        output_dir: Directory where predictions will be written.
        model_dir: Trained nnU-Net model directory containing fold_0/checkpoint_final.pth.
        predict_bin: Path to nnUNetv2_predict executable.
        cuda_device: Target GPU index (e.g. '2' or '3').
        min_volume_mm3: Minimum lesion volume threshold in cubic millimeters.
        
    Returns:
        output_mask_path: Path to the generated 3D lesion mask.
        lesion_findings: List of detected individual lesion ROIs with spatial metrics.
    """
    input_nii_path = Path(input_nii_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_in_dir = output_dir / "temp_input"
    temp_in_dir.mkdir(parents=True, exist_ok=True)

    case_id = "sample_case"
    # nnU-Net expects input file named <case_id>_0000.nii.gz
    formatted_input = temp_in_dir / f"{case_id}_0000.nii.gz"
    if not formatted_input.exists():
        shutil.copyfile(input_nii_path, formatted_input)

    expected_output_mask = output_dir / f"{case_id}.nii.gz"

    # Configure nnUNet environment variables
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
        "-device", "cuda",  # nnU-Net requires 'cuda', 'cpu', or 'mps' (CUDA_VISIBLE_DEVICES controls index)
        "--disable_progress_bar",
    ]

    print(f"[*] Running nnU-Net inference on GPU {cuda_device}...")
    result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"[!] nnU-Net prediction error stderr:\n{result.stderr}")
        raise RuntimeError(f"nnU-Net prediction failed with exit code {result.returncode}: {result.stderr}")

    if not expected_output_mask.exists():
        raise FileNotFoundError(f"Expected nnU-Net output mask not found at {expected_output_mask}")

    # Clean up temp input
    shutil.rmtree(temp_in_dir, ignore_errors=True)

    # Post-process mask: separate multiple lesions by connected components
    lesion_findings = analyze_lesion_components(
        mask_path=expected_output_mask,
        min_volume_mm3=min_volume_mm3
    )

    return expected_output_mask, lesion_findings


def analyze_lesion_components(
    mask_path: str | Path,
    min_volume_mm3: float = 30.0
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
