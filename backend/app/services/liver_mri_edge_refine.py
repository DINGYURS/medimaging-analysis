from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import nibabel as nib
import numpy as np
from scipy import ndimage


DEFAULT_ALPHA = 2.0
DEFAULT_DILATION_ITERATIONS = 3
MIN_SLICE_AREA_RATIO = 0.55
MAX_SLICE_AREA_RATIO = 1.45
TINY_COMPONENT_MAX_VOLUME_ML = 0.05
TINY_COMPONENT_MAX_ACTIVE_SLICES = 2
TINY_COMPONENT_MAX_MEDIAN_PROBABILITY = 0.7
AREA_RATIO_WARNING_MIN = 0.8
AREA_RATIO_WARNING_MAX = 1.2


def _active_slices(mask: np.ndarray) -> set[int]:
    return set(np.flatnonzero(mask.any(axis=(0, 1))).tolist())


def _topology_stats(raw: np.ndarray, refined: np.ndarray) -> dict[str, Any]:
    structure = ndimage.generate_binary_structure(3, 1)
    raw_labels, raw_count = ndimage.label(raw, structure=structure)
    refined_labels, refined_count = ndimage.label(refined, structure=structure)
    raw_to_refined: dict[int, set[int]] = {}
    refined_to_raw: dict[int, set[int]] = {}
    for raw_label in range(1, int(raw_count) + 1):
        overlaps = set(np.unique(refined_labels[raw_labels == raw_label]).tolist())
        overlaps.discard(0)
        raw_to_refined[raw_label] = overlaps
    for refined_label in range(1, int(refined_count) + 1):
        overlaps = set(np.unique(raw_labels[refined_labels == refined_label]).tolist())
        overlaps.discard(0)
        refined_to_raw[refined_label] = overlaps
    active_slices_preserved = _active_slices(raw) == _active_slices(refined)
    one_to_one = all(len(labels) == 1 for labels in raw_to_refined.values()) and all(
        len(labels) == 1 for labels in refined_to_raw.values()
    )
    return {
        "preserved": bool(active_slices_preserved and one_to_one),
        "active_slices_preserved": active_slices_preserved,
        "raw_component_count": int(raw_count),
        "refined_component_count": int(refined_count),
        "split_raw_component_count": sum(len(labels) > 1 for labels in raw_to_refined.values()),
        "removed_raw_component_count": sum(len(labels) == 0 for labels in raw_to_refined.values()),
        "merged_refined_component_count": sum(len(labels) > 1 for labels in refined_to_raw.values()),
        "new_refined_component_count": sum(len(labels) == 0 for labels in refined_to_raw.values()),
    }


def _tiny_low_confidence_components(
    mask: np.ndarray,
    probability: np.ndarray,
    *,
    voxel_volume_ml: float,
) -> list[dict[str, Any]]:
    labels, component_count = ndimage.label(
        mask,
        structure=ndimage.generate_binary_structure(3, 1),
    )
    candidates: list[dict[str, Any]] = []
    for component_index in range(1, int(component_count) + 1):
        component = labels == component_index
        voxel_count = int(component.sum())
        active_slice_count = len(_active_slices(component))
        values = probability[component]
        if values.size == 0:
            continue
        volume_ml = voxel_count * max(0.0, float(voxel_volume_ml))
        median_probability = float(np.median(values))
        if (
            volume_ml < TINY_COMPONENT_MAX_VOLUME_ML
            and active_slice_count <= TINY_COMPONENT_MAX_ACTIVE_SLICES
            and median_probability < TINY_COMPONENT_MAX_MEDIAN_PROBABILITY
        ):
            candidates.append(
                {
                    "component_index": component_index,
                    "voxel_count": voxel_count,
                    "volume_ml": round(volume_ml, 6),
                    "active_slice_count": active_slice_count,
                    "median_probability": round(median_probability, 4),
                    "max_probability": round(float(values.max()), 4),
                }
            )
    return candidates


def _voxel_volume_ml(image: nib.Nifti1Image) -> float:
    try:
        volume_mm3 = abs(float(np.linalg.det(np.asarray(image.affine)[:3, :3])))
    except (TypeError, ValueError, np.linalg.LinAlgError):
        volume_mm3 = 0.0
    if not np.isfinite(volume_mm3) or volume_mm3 <= 0.0:
        zooms = image.header.get_zooms()[:3]
        volume_mm3 = float(np.prod(zooms)) if len(zooms) == 3 else 0.0
    return volume_mm3 / 1000.0 if np.isfinite(volume_mm3) else 0.0


def _area_ratio_stats(area_ratios: Sequence[float]) -> dict[str, Any]:
    if not area_ratios:
        return {
            "area_ratio_min": None,
            "area_ratio_max": None,
            "area_ratio_warning_count": 0,
        }
    return {
        "area_ratio_min": round(float(min(area_ratios)), 4),
        "area_ratio_max": round(float(max(area_ratios)), 4),
        "area_ratio_warning_count": sum(
            ratio < AREA_RATIO_WARNING_MIN or ratio > AREA_RATIO_WARNING_MAX
            for ratio in area_ratios
        ),
    }


def _probability_xyz(probability_npz_path: Path, mask_shape: tuple[int, int, int]) -> np.ndarray:
    with np.load(probability_npz_path) as payload:
        if "probabilities" not in payload:
            raise ValueError("probability archive does not contain 'probabilities'")
        probabilities = np.asarray(payload["probabilities"], dtype=np.float32)

    # nnU-Net exports class, Z, Y, X.  The class-bearing shape is an
    # unambiguous contract even when X == Y == Z; do not infer orientation
    # from equal spatial dimensions.
    if probabilities.ndim == 4:
        expected = (2, *tuple(reversed(mask_shape)))
        if tuple(probabilities.shape) != expected:
            raise ValueError(
                f"expected nnUNet class/ZYX probabilities {expected}, got {tuple(probabilities.shape)}"
            )
        return np.asarray(probabilities[1].transpose(2, 1, 0), dtype=np.float32)

    # Legacy services may return a foreground-only 3-D array.  Retain the
    # previous shape compatibility for those responses; cubic 3-D arrays
    # remain inherently ambiguous without class metadata.
    if probabilities.ndim == 3:
        if tuple(probabilities.shape) == mask_shape:
            return probabilities
        if tuple(probabilities.shape) == tuple(reversed(mask_shape)):
            return np.transpose(probabilities, (2, 1, 0))
    raise ValueError(
        f"probability shape {tuple(probabilities.shape)} does not match mask shape {mask_shape}"
    )


def _robust_evidence(
    image: np.ndarray,
    *,
    context: np.ndarray,
    foreground_seed: np.ndarray,
    background_seed: np.ndarray,
) -> tuple[np.ndarray, float]:
    finite_values = image[np.isfinite(image) & context]
    if finite_values.size < 4:
        return np.zeros_like(image, dtype=np.float32), 0.0
    low, high = np.percentile(finite_values, [1, 99])
    if high <= low:
        return np.zeros_like(image, dtype=np.float32), 0.0
    normalized = np.clip((image - low) / (high - low), 0.0, 1.0).astype(np.float32)
    foreground_values = normalized[foreground_seed]
    background_values = normalized[background_seed]
    if foreground_values.size < 1 or background_values.size < 1:
        return np.zeros_like(image, dtype=np.float32), 0.0

    foreground_center = float(np.median(foreground_values))
    background_center = float(np.median(background_values))
    foreground_scale = max(
        float(np.median(np.abs(foreground_values - foreground_center))) * 1.4826,
        0.06,
    )
    background_scale = max(
        float(np.median(np.abs(background_values - background_center))) * 1.4826,
        0.06,
    )
    evidence = (
        np.abs(normalized - background_center) / background_scale
        - np.abs(normalized - foreground_center) / foreground_scale
    )
    separation = abs(foreground_center - background_center) / (foreground_scale + background_scale)
    return np.clip(evidence, -4.0, 4.0).astype(np.float32), float(separation)


def refine_liver_mri_slice(
    image_slices: Sequence[np.ndarray],
    probability_slice: np.ndarray,
    *,
    alpha: float = DEFAULT_ALPHA,
    dilation_iterations: int = DEFAULT_DILATION_ITERATIONS,
) -> tuple[np.ndarray, dict[str, Any]]:
    probability = np.asarray(probability_slice, dtype=np.float32)
    prior = probability >= 0.5
    prior_pixels = int(prior.sum())
    if prior_pixels < 4 or len(image_slices) != 5:
        return prior, {"applied": False, "reason": "invalid_or_too_small", "prior_pixels": prior_pixels}
    if any(np.asarray(image).shape != prior.shape for image in image_slices):
        return prior, {"applied": False, "reason": "shape_mismatch", "prior_pixels": prior_pixels}

    structure = np.ones((3, 3), dtype=bool)
    allowed = ndimage.binary_dilation(
        prior,
        structure=structure,
        iterations=max(1, int(dilation_iterations)),
    )
    context = ndimage.binary_dilation(allowed, structure=structure, iterations=1)
    foreground_seed = (probability >= 0.8) & prior
    if int(foreground_seed.sum()) < 2:
        foreground_seed = ndimage.binary_erosion(prior, structure=structure, iterations=1)
    if int(foreground_seed.sum()) < 1:
        distance = ndimage.distance_transform_edt(prior)
        foreground_seed = prior & (distance >= max(1.0, float(distance.max()) * 0.5))
    background_seed = context & ~allowed & (probability <= 0.3)
    if int(foreground_seed.sum()) < 1 or int(background_seed.sum()) < 2:
        return prior, {
            "applied": False,
            "reason": "missing_seed",
            "prior_pixels": prior_pixels,
            "foreground_seed_pixels": int(foreground_seed.sum()),
            "background_seed_pixels": int(background_seed.sum()),
        }

    evidences: list[np.ndarray] = []
    separations: list[float] = []
    for image in image_slices:
        evidence, separation = _robust_evidence(
            np.asarray(image, dtype=np.float32),
            context=context,
            foreground_seed=foreground_seed,
            background_seed=background_seed,
        )
        evidences.append(evidence)
        separations.append(separation)

    weights = np.asarray(separations, dtype=np.float32)
    cutoff = float(np.percentile(weights, 60))
    weights = np.where(weights >= cutoff, weights, 0.0)
    if float(weights.sum()) <= 1e-6:
        return prior, {
            "applied": False,
            "reason": "insufficient_multisequence_contrast",
            "prior_pixels": prior_pixels,
            "channel_separations": [round(value, 4) for value in separations],
        }
    combined_evidence = sum(
        float(weight) * evidence for weight, evidence in zip(weights, evidences)
    ) / float(weights.sum())

    clipped_probability = np.clip(probability, 1e-4, 1.0 - 1e-4)
    probability_logit = np.log(clipped_probability / (1.0 - clipped_probability))
    selected = allowed & ((probability_logit + float(alpha) * combined_evidence) >= 0.0)
    selected |= foreground_seed
    selected = ndimage.binary_fill_holes(selected)
    labels, _ = ndimage.label(selected, structure=structure)
    connected_labels = np.unique(labels[foreground_seed & (labels > 0)])
    if connected_labels.size:
        selected = np.isin(labels, connected_labels)
    else:
        selected = prior

    selected_pixels = int(selected.sum())
    area_ratio = selected_pixels / max(prior_pixels, 1)
    if (
        selected_pixels < 2
        or area_ratio < MIN_SLICE_AREA_RATIO
        or area_ratio > MAX_SLICE_AREA_RATIO
    ):
        return prior, {
            "applied": False,
            "reason": "area_gate",
            "prior_pixels": prior_pixels,
            "selected_pixels": selected_pixels,
            "area_ratio": round(area_ratio, 4),
            "channel_separations": [round(value, 4) for value in separations],
        }

    return selected, {
        "applied": not np.array_equal(selected, prior),
        "reason": "accepted" if not np.array_equal(selected, prior) else "no_effect",
        "prior_pixels": prior_pixels,
        "selected_pixels": selected_pixels,
        "area_ratio": round(area_ratio, 4),
        "channel_separations": [round(value, 4) for value in separations],
        "selected_channels": [index for index, weight in enumerate(weights.tolist()) if weight > 0],
    }


def _refined_mask_path(mask_path: Path) -> Path:
    if mask_path.name.endswith(".nii.gz"):
        return mask_path.with_name(f"{mask_path.name[:-7]}_mri_edge_refined.nii.gz")
    return mask_path.with_name(f"{mask_path.stem}_mri_edge_refined{mask_path.suffix}")


def refine_liver_mri_mask_with_probabilities(
    *,
    mask_nifti_path: str | Path,
    probability_npz_path: str | Path,
    channel_paths: Sequence[str | Path],
    alpha: float = DEFAULT_ALPHA,
    dilation_iterations: int = DEFAULT_DILATION_ITERATIONS,
) -> tuple[Path, dict[str, Any]]:
    mask_path = Path(mask_nifti_path)
    probability_path = Path(probability_npz_path)
    paths = [Path(path) for path in channel_paths]
    if not mask_path.is_file() or not probability_path.is_file() or len(paths) != 5:
        return mask_path, {"enabled": True, "applied": False, "reason": "missing_inputs"}

    mask_image = nib.load(str(mask_path))
    mask_xyz = np.asarray(mask_image.dataobj) > 0
    if mask_xyz.ndim != 3:
        return mask_path, {"enabled": True, "applied": False, "reason": "invalid_mask_shape"}
    try:
        probability_xyz = _probability_xyz(probability_path, tuple(mask_xyz.shape))
    except (OSError, ValueError) as exc:
        return mask_path, {
            "enabled": True,
            "applied": False,
            "reason": "invalid_probabilities",
            "detail": str(exc),
        }

    images: list[np.ndarray] = []
    for path in paths:
        if not path.is_file():
            return mask_path, {"enabled": True, "applied": False, "reason": "missing_channel"}
        image = nib.load(str(path))
        if tuple(image.shape) != tuple(mask_xyz.shape) or not np.allclose(
            image.affine,
            mask_image.affine,
            rtol=1e-5,
            atol=1e-4,
        ):
            return mask_path, {"enabled": True, "applied": False, "reason": "channel_geometry_mismatch"}
        images.append(np.asarray(image.dataobj, dtype=np.float32))

    zero_filled_channels = [
        index
        for index, image in enumerate(images)
        if not np.any(np.isfinite(image) & (np.abs(image) > 1e-8))
    ]
    original_voxels = int(mask_xyz.sum())
    active_slices = np.flatnonzero(mask_xyz.any(axis=(0, 1)))
    tiny_candidates = _tiny_low_confidence_components(
        mask_xyz,
        probability_xyz,
        voxel_volume_ml=_voxel_volume_ml(mask_image),
    )
    common_stats: dict[str, Any] = {
        "enabled": True,
        "method": "probability_multisequence_boundary",
        "alpha": float(alpha),
        "dilation_iterations": int(dilation_iterations),
        "active_slices": int(active_slices.size),
        "original_voxels": original_voxels,
        "zero_filled_channels": zero_filled_channels,
        "tiny_low_confidence_components": tiny_candidates,
        "tiny_low_confidence_component_count": len(tiny_candidates),
    }
    if zero_filled_channels:
        return mask_path, {
            **common_stats,
            "applied": False,
            "reason": "missing_modality_conservative_fallback",
            "changed_slices": 0,
            "refined_voxels": original_voxels,
            "voxel_ratio": 1.0 if original_voxels else 0.0,
            "topology": _topology_stats(mask_xyz, mask_xyz),
            **_area_ratio_stats([]),
        }

    refined = np.zeros_like(mask_xyz, dtype=bool)
    changed_slices = 0
    fallback_reasons: dict[str, int] = {}
    selected_channel_counts = [0, 0, 0, 0, 0]
    area_ratios: list[float] = []
    for z in active_slices.tolist():
        selected, slice_stats = refine_liver_mri_slice(
            [image[:, :, z] for image in images],
            probability_xyz[:, :, z],
            alpha=alpha,
            dilation_iterations=dilation_iterations,
        )
        refined[:, :, z] = selected
        if bool(slice_stats.get("applied")):
            changed_slices += 1
        else:
            reason = str(slice_stats.get("reason") or "fallback")
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        if slice_stats.get("area_ratio") is not None:
            area_ratios.append(float(slice_stats["area_ratio"]))
        for channel in slice_stats.get("selected_channels") or []:
            selected_channel_counts[int(channel)] += 1

    refined_voxels = int(refined.sum())
    topology = _topology_stats(mask_xyz, refined)
    refinement_stats = {
        **common_stats,
        "changed_slices": changed_slices,
        "refined_voxels": refined_voxels,
        "voxel_ratio": round(refined_voxels / max(original_voxels, 1), 4),
        "fallback_reasons": fallback_reasons,
        "selected_channel_counts": selected_channel_counts,
        "topology": topology,
        **_area_ratio_stats(area_ratios),
    }
    if changed_slices <= 0 or np.array_equal(refined, mask_xyz):
        return mask_path, {
            **refinement_stats,
            "applied": False,
            "reason": "no_effect",
        }

    if not bool(topology["preserved"]):
        return mask_path, {
            **refinement_stats,
            "applied": False,
            "reason": "topology_gate",
            "attempted_refined_voxels": refined_voxels,
            "refined_voxels": original_voxels,
            "voxel_ratio": 1.0 if original_voxels else 0.0,
        }

    output_path = _refined_mask_path(mask_path)
    nib.save(
        nib.Nifti1Image(refined.astype(np.uint8), mask_image.affine, mask_image.header),
        str(output_path),
    )
    return output_path, {
        **refinement_stats,
        "applied": True,
        "reason": "accepted",
    }
