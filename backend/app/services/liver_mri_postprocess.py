"""Conservative removal of isolated, weak MRI lesion predictions.

Only model probabilities and physical component volumes enter the filter.
It never uses manual labels, CT intensities, a largest-component assumption,
or dilation/erosion of a retained lesion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from scipy import ndimage


POSTPROCESS_VERSION = "liver_mri_fp_cleanup_v1"
SMALL_VOLUME_MM3 = 200.0
SMALL_MEAN_PROBABILITY = 0.90
WEAK_VOLUME_MM3 = 1000.0
WEAK_MEAN_PROBABILITY = 0.80
CONNECTIVITY = np.ones((3, 3, 3), dtype=bool)


def cleanup_liver_mri_components(
    mask: np.ndarray,
    probability: np.ndarray,
    *,
    voxel_volume_mm3: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    raw = np.asarray(mask)
    scores = np.asarray(probability, dtype=np.float32)
    if raw.ndim != 3 or scores.shape != raw.shape:
        raise ValueError("mask/probability must share one 3D grid")
    if not np.isfinite(raw).all() or not np.isin(raw, [0, 1]).all():
        raise ValueError("mask must contain finite binary labels")
    if not np.isfinite(scores).all() or np.any(scores < 0) or np.any(scores > 1):
        raise ValueError("foreground probabilities must be finite and in [0, 1]")
    if not np.isfinite(voxel_volume_mm3) or voxel_volume_mm3 <= 0:
        raise ValueError("voxel volume must be finite and positive")
    raw = raw.astype(bool, copy=False)
    labels, count = ndimage.label(raw, structure=CONNECTIVITY)
    result = raw.copy()
    removed = []
    for index, bounds in enumerate(ndimage.find_objects(labels), start=1):
        if bounds is None:
            continue
        region = labels[bounds] == index
        voxels = int(region.sum())
        volume = voxels * float(voxel_volume_mm3)
        mean = float(np.mean(scores[bounds][region], dtype=np.float64))
        small_weak = volume < SMALL_VOLUME_MM3 and mean < SMALL_MEAN_PROBABILITY
        medium_weak = volume < WEAK_VOLUME_MM3 and mean < WEAK_MEAN_PROBABILITY
        if small_weak or medium_weak:
            result[bounds][region] = False
            removed.append({
                "component_index": index,
                "voxels": voxels,
                "volume_mm3": volume,
                "mean_probability": mean,
                "reason": "small_weak_component" if small_weak else "weak_component",
            })
    stats = {
        "enabled": True,
        "version": POSTPROCESS_VERSION,
        "method": "physical_volume_and_component_confidence",
        "connectivity": 26,
        "parameters": {
            "small_volume_mm3": SMALL_VOLUME_MM3,
            "small_mean_probability": SMALL_MEAN_PROBABILITY,
            "weak_volume_mm3": WEAK_VOLUME_MM3,
            "weak_mean_probability": WEAK_MEAN_PROBABILITY,
        },
        "input_voxels": int(raw.sum()),
        "output_voxels": int(result.sum()),
        "removed_voxels": int(raw.sum()) - int(result.sum()),
        "input_components": int(count),
        "output_components": int(count) - len(removed),
        "removed_component_count": len(removed),
        "removed_components": removed,
        "applied": bool(removed),
        "reason": "removed_weak_components" if removed else "no_weak_components",
    }
    return result, stats


def nnunet_probability_xyz(path: str | Path, shape_xyz: tuple[int, ...]) -> np.ndarray:
    """nnUNet SimpleITK export is class, Z, Y, X, including cubic grids."""
    with np.load(path, allow_pickle=False) as payload:
        probabilities = np.asarray(payload["probabilities"])
        expected = (2, *reversed(shape_xyz))
        if probabilities.shape != expected:
            raise ValueError(f"expected nnUNet class/ZYX probabilities {expected}, got {probabilities.shape}")
        return np.asarray(probabilities[1].transpose(2, 1, 0), dtype=np.float32)


def postprocess_liver_mri_mask(
    *,
    mask_nifti_path: str | Path,
    probability_npz_path: str | Path | None,
    output_path: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    mask_path = Path(mask_nifti_path)
    unavailable = {"enabled": True, "version": POSTPROCESS_VERSION, "applied": False}
    if not probability_npz_path or not Path(probability_npz_path).is_file():
        return mask_path, {**unavailable, "reason": "probabilities_unavailable"}
    try:
        image = nib.load(str(mask_path))
        raw = np.asanyarray(image.dataobj)
        probability = nnunet_probability_xyz(probability_npz_path, image.shape)
        voxel_volume = abs(float(np.linalg.det(image.affine[:3, :3])))
        cleaned, stats = cleanup_liver_mri_components(raw, probability, voxel_volume_mm3=voxel_volume)
    except (OSError, ValueError, KeyError) as exc:
        return mask_path, {**unavailable, "reason": "invalid_postprocess_inputs", "detail": str(exc)}
    if not stats["applied"]:
        return mask_path, stats
    output = Path(output_path) if output_path else mask_path.with_name(
        mask_path.name.removesuffix(".nii.gz").removesuffix(".nii") + "_fp_cleaned_v1.nii.gz"
    )
    if output.resolve() == mask_path.resolve():
        raise ValueError("postprocessing must preserve the original mask")
    output.parent.mkdir(parents=True, exist_ok=True)
    header = image.header.copy()
    header.set_data_dtype(np.uint8)
    nib.save(nib.Nifti1Image(cleaned.astype(np.uint8), image.affine, header), str(output))
    return output, stats
