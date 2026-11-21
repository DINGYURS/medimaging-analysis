"""Custom lung nodule features layered on top of PyRadiomics.

The functions here intentionally use numpy/scipy only. They are computed on the
cropped lesion ROI, so simple CPU implementations are fast enough and avoid
adding OpenCV/skimage runtime dependencies to the platform.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull

CUSTOM_FEATURE_KEYS = [
    "custom_ctr_threshold_hu",
    "custom_ctr_percent",
    "custom_solid_volume_mm3",
    "custom_solid_voxel_count",
    "custom_nodule_volume_mm3",
    "custom_nodule_voxel_count",
    "custom_area_2d_mm2",
    "custom_perimeter_2d_mm",
    "custom_equivalent_diameter_2d_mm",
    "custom_equivalent_diameter_3d_mm",
    "custom_convex_area_2d_mm2",
    "custom_convex_perimeter_2d_mm",
    "custom_circularity_2d",
    "custom_radial_distance_std_2d_mm",
    "custom_margin_mean_boundary_gradient_hu_per_mm",
    "custom_margin_mean_abs_boundary_gradient_hu_per_mm",
    "custom_margin_std_boundary_gradient_hu_per_mm",
    "custom_edge_sharpness_acutance_3d",
    "custom_hsag",
    "custom_cdf_slope_mean",
    "custom_cdf_slope_std",
    "custom_cdf_slope_skewness",
    "custom_cdf_slope_kurtosis",
    "custom_cdf_hu_min",
    "custom_cdf_hu_max",
    "custom_boundary_convexity",
    "custom_boundary_entropy_ratio",
    "custom_contour_convexity",
    "custom_contour_solidity",
    "custom_contour_circularity",
    "custom_contour_compactness",
]


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _boundary_mask(mask2d: np.ndarray) -> np.ndarray:
    mask2d = np.asarray(mask2d, dtype=bool)
    if not mask2d.any():
        return np.zeros_like(mask2d, dtype=bool)
    return mask2d & ~ndi.binary_erosion(mask2d)


def _perimeter_mm(mask2d: np.ndarray, spacing_yx: tuple[float, float]) -> float:
    boundary = _boundary_mask(mask2d)
    if not boundary.any():
        return 0.0
    sy, sx = spacing_yx
    horizontal = np.logical_xor(boundary[:, 1:], boundary[:, :-1]).sum() * sy
    vertical = np.logical_xor(boundary[1:, :], boundary[:-1, :]).sum() * sx
    return float(horizontal + vertical)


def _convex_hull_area_perimeter(mask2d: np.ndarray, spacing_yx: tuple[float, float]) -> tuple[float | None, float | None]:
    coords = np.column_stack(np.nonzero(np.asarray(mask2d, dtype=bool)))
    if coords.shape[0] < 3:
        return None, None
    sy, sx = spacing_yx
    points_mm = np.column_stack((coords[:, 1] * sx, coords[:, 0] * sy))
    try:
        hull = ConvexHull(points_mm)
    except Exception:
        return None, None
    vertices = points_mm[hull.vertices]
    x = vertices[:, 0]
    y = vertices[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    perimeter = float(np.linalg.norm(np.diff(np.vstack([vertices, vertices[0]]), axis=0), axis=1).sum())
    return _finite(area), _finite(perimeter)


def _largest_slice(mask_zyx: np.ndarray) -> int:
    areas = np.asarray(mask_zyx, dtype=bool).reshape(mask_zyx.shape[0], -1).sum(axis=1)
    return int(np.argmax(areas))


def _radial_distance_std_mm(mask2d: np.ndarray, spacing_yx: tuple[float, float]) -> float | None:
    boundary = _boundary_mask(mask2d)
    coords = np.column_stack(np.nonzero(boundary))
    all_coords = np.column_stack(np.nonzero(mask2d))
    if coords.size == 0 or all_coords.size == 0:
        return None
    sy, sx = spacing_yx
    centroid = np.column_stack(((all_coords[:, 0] + 0.5) * sy, (all_coords[:, 1] + 0.5) * sx)).mean(axis=0)
    boundary_mm = np.column_stack(((coords[:, 0] + 0.5) * sy, (coords[:, 1] + 0.5) * sx))
    return _finite(np.linalg.norm(boundary_mm - centroid, axis=1).std())


def _sample_image_2d(image: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    return ndi.map_coordinates(
        np.asarray(image, dtype=np.float32),
        [np.asarray(rows, dtype=np.float64), np.asarray(cols, dtype=np.float64)],
        order=1,
        mode="nearest",
    )


def _boundary_gradients(image2d: np.ndarray, mask2d: np.ndarray, spacing_yx: tuple[float, float]) -> np.ndarray:
    mask2d = np.asarray(mask2d, dtype=bool)
    coords = np.column_stack(np.nonzero(_boundary_mask(mask2d)))
    if coords.size == 0:
        return np.array([], dtype=np.float32)
    sy, sx = spacing_yx
    step_mm = float(min(sy, sx))
    inside = ndi.distance_transform_edt(mask2d, sampling=(sy, sx))
    outside = ndi.distance_transform_edt(~mask2d, sampling=(sy, sx))
    signed = inside - outside
    gy, gx = np.gradient(signed, sy, sx)
    rows = coords[:, 0].astype(np.float64)
    cols = coords[:, 1].astype(np.float64)
    ny = gy[coords[:, 0], coords[:, 1]].astype(np.float64)
    nx = gx[coords[:, 0], coords[:, 1]].astype(np.float64)
    norm = np.sqrt(ny**2 + nx**2) + 1e-8
    ny /= norm
    nx /= norm
    gradient_sum = np.zeros(coords.shape[0], dtype=np.float64)
    for i in range(1, 4):
        d_mm = step_mm * i
        fg = _sample_image_2d(image2d, rows + ny * d_mm / sy, cols + nx * d_mm / sx)
        bg = _sample_image_2d(image2d, rows - ny * d_mm / sy, cols - nx * d_mm / sx)
        gradient_sum += (fg - bg) / (2.0 * d_mm)
    return (gradient_sum / 3.0).astype(np.float32)


def _entropy(values: np.ndarray, bins: int = 32) -> float | None:
    values = np.asarray(values, dtype=np.float32)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None
    hist, _ = np.histogram(values, bins=bins)
    prob = hist.astype(np.float64)
    prob = prob[prob > 0] / max(1.0, float(prob.sum()))
    return _finite(-(prob * np.log2(prob)).sum())


def _otsu_threshold(values: np.ndarray, bins: int = 256) -> float | None:
    values = np.asarray(values, dtype=np.float32)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None
    v_min = float(values.min())
    v_max = float(values.max())
    if np.isclose(v_min, v_max):
        return _finite(v_min)

    hist, edges = np.histogram(values, bins=bins, range=(v_min, v_max))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return None
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight_background = np.cumsum(hist)
    weight_foreground = total - weight_background
    mean_background = np.cumsum(hist * centers) / np.maximum(weight_background, 1e-8)
    mean_foreground = (np.cumsum((hist * centers)[::-1]) / np.maximum(np.cumsum(hist[::-1]), 1e-8))[::-1]
    variance_between = weight_background[:-1] * weight_foreground[:-1] * (
        mean_background[:-1] - mean_foreground[1:]
    ) ** 2
    if variance_between.size == 0:
        return None
    return _finite(float(centers[int(np.argmax(variance_between))]))


def _convexity_by_slice(mask_zyx: np.ndarray, spacing_yx: tuple[float, float]) -> float | None:
    vals: list[float] = []
    for mask2d in np.asarray(mask_zyx, dtype=bool):
        area = float(mask2d.sum() * spacing_yx[0] * spacing_yx[1])
        hull_area, _ = _convex_hull_area_perimeter(mask2d, spacing_yx)
        if hull_area and hull_area > 0:
            vals.append(area / hull_area)
    return _finite(np.mean(vals)) if vals else None


def _entropy_ratio(image_zyx: np.ndarray, mask_zyx: np.ndarray) -> float | None:
    core_vals: list[float] = []
    boundary_vals: list[float] = []
    for z, mask2d in enumerate(mask_zyx):
        if not mask2d.any():
            continue
        core = ndi.binary_erosion(mask2d, iterations=2)
        boundary = mask2d & ~core
        if not core.any() or not boundary.any():
            continue
        core_entropy = _entropy(image_zyx[z][core])
        boundary_entropy = _entropy(image_zyx[z][boundary])
        if core_entropy is not None and boundary_entropy is not None and core_entropy > 0:
            core_vals.append(core_entropy)
            boundary_vals.append(boundary_entropy)
    if not core_vals or not boundary_vals:
        return None
    return _finite(float(np.mean(boundary_vals) / np.mean(core_vals)))


def _cdf_features(image_zyx: np.ndarray, mask_zyx: np.ndarray) -> dict[str, float | None]:
    z = _largest_slice(mask_zyx)
    values = np.asarray(image_zyx[z], dtype=np.float32)[mask_zyx[z]]
    values = values[np.isfinite(values)]
    if values.size < 2:
        return {}
    v_min = float(values.min())
    v_max = float(values.max())
    if np.isclose(v_min, v_max):
        slopes = np.zeros(1, dtype=np.float64)
    else:
        hist, edges = np.histogram(values, bins=128, range=(v_min, v_max))
        prob = hist.astype(np.float64) / max(1.0, float(hist.sum()))
        slopes = prob / (np.diff(edges) + 1e-8)
    mean = float(slopes.mean())
    std = float(slopes.std())
    if std < 1e-8:
        skewness = 0.0
        kurtosis = 0.0
    else:
        normalized = (slopes - mean) / (std + 1e-8)
        skewness = float(np.mean(normalized**3))
        kurtosis = float(np.mean(normalized**4))
    return {
        "custom_cdf_slope_mean": _finite(mean),
        "custom_cdf_slope_std": _finite(std),
        "custom_cdf_slope_skewness": _finite(skewness),
        "custom_cdf_slope_kurtosis": _finite(kurtosis),
        "custom_cdf_hu_min": _finite(v_min),
        "custom_cdf_hu_max": _finite(v_max),
    }


def compute_lung_nodule_custom_features(
    image_xyz: np.ndarray,
    mask_xyz: np.ndarray,
    spacing_xyz: tuple[float, float, float],
    *,
    solid_threshold_hu: float | None = None,
) -> dict[str, float]:
    image_zyx = np.transpose(np.asarray(image_xyz, dtype=np.float32), (2, 1, 0))
    mask_zyx = np.transpose(np.asarray(mask_xyz) > 0, (2, 1, 0))
    spacing_zyx = (float(spacing_xyz[2]), float(spacing_xyz[1]), float(spacing_xyz[0]))
    spacing_yx = (spacing_zyx[1], spacing_zyx[2])
    if image_zyx.shape != mask_zyx.shape or not mask_zyx.any():
        return {}

    voxel_volume = float(spacing_xyz[0] * spacing_xyz[1] * spacing_xyz[2])
    nodule_voxels = int(mask_zyx.sum())
    threshold = _finite(solid_threshold_hu) if solid_threshold_hu is not None else _otsu_threshold(image_zyx[mask_zyx])
    if threshold is None:
        threshold = -150.0
    solid = mask_zyx & (image_zyx >= float(threshold))
    solid_voxels = int(solid.sum())
    z = _largest_slice(mask_zyx)
    mask2d = mask_zyx[z]
    image2d = image_zyx[z]
    area2d = float(mask2d.sum() * spacing_yx[0] * spacing_yx[1])
    perimeter = _perimeter_mm(mask2d, spacing_yx)
    convex_area, convex_perimeter = _convex_hull_area_perimeter(mask2d, spacing_yx)
    nodule_volume = float(nodule_voxels * voxel_volume)
    circularity = None
    if convex_perimeter and convex_perimeter > 0:
        circularity = 4.0 * math.pi * area2d / (convex_perimeter**2)
    equivalent_diameter_2d = math.sqrt(4.0 * area2d / math.pi) if area2d > 0 else None
    equivalent_diameter_3d = (6.0 * nodule_volume / math.pi) ** (1.0 / 3.0) if nodule_volume > 0 else None

    gradients: list[np.ndarray] = []
    for zi in range(mask_zyx.shape[0]):
        if mask_zyx[zi].any():
            grads = _boundary_gradients(image_zyx[zi], mask_zyx[zi], spacing_yx)
            if grads.size:
                gradients.append(grads)
    all_gradients = np.concatenate(gradients).astype(np.float64) if gradients else np.array([], dtype=np.float64)
    if all_gradients.size:
        grad_range = float(all_gradients.max() - all_gradients.min())
        q1 = float(np.percentile(all_gradients, 25))
        q3 = float(np.percentile(all_gradients, 75))
        dmax = float(np.abs(all_gradients).max()) + 1e-8
        acutance = float(np.sqrt(np.mean(all_gradients**2)) / dmax)
        hsag = 0.0 if grad_range < 1e-8 else float((q3 - q1) / (grad_range + 1e-8))
    else:
        acutance = hsag = None

    contour_area = float(mask2d.sum())
    contour_perimeter = max(perimeter, 1e-8)
    contour_convexity = _finite((convex_perimeter / contour_perimeter) if convex_perimeter else None)
    contour_solidity = _finite((area2d / convex_area) if convex_area else None)
    contour_compactness = _finite(4.0 * math.pi * area2d / (perimeter**2)) if perimeter > 0 else None
    contour_circularity = None
    boundary = np.column_stack(np.nonzero(_boundary_mask(mask2d)))
    if boundary.shape[0] > 2:
        centroid = np.column_stack(np.nonzero(mask2d)).mean(axis=0)
        dists = np.linalg.norm(boundary - centroid, axis=1)
        if float(dists.std()) > 1e-8:
            contour_circularity = _finite(float(dists.mean() / dists.std()))

    result: dict[str, float | None] = {
        "custom_ctr_threshold_hu": _finite(threshold),
        "custom_ctr_percent": _finite(solid_voxels / nodule_voxels * 100.0),
        "custom_solid_volume_mm3": _finite(solid_voxels * voxel_volume),
        "custom_solid_voxel_count": float(solid_voxels),
        "custom_nodule_volume_mm3": _finite(nodule_volume),
        "custom_nodule_voxel_count": float(nodule_voxels),
        "custom_area_2d_mm2": _finite(area2d),
        "custom_perimeter_2d_mm": _finite(perimeter),
        "custom_equivalent_diameter_2d_mm": _finite(equivalent_diameter_2d),
        "custom_equivalent_diameter_3d_mm": _finite(equivalent_diameter_3d),
        "custom_convex_area_2d_mm2": convex_area,
        "custom_convex_perimeter_2d_mm": convex_perimeter,
        "custom_circularity_2d": _finite(circularity),
        "custom_radial_distance_std_2d_mm": _radial_distance_std_mm(mask2d, spacing_yx),
        "custom_margin_mean_boundary_gradient_hu_per_mm": _finite(all_gradients.mean()) if all_gradients.size else None,
        "custom_margin_mean_abs_boundary_gradient_hu_per_mm": _finite(np.abs(all_gradients).mean()) if all_gradients.size else None,
        "custom_margin_std_boundary_gradient_hu_per_mm": _finite(all_gradients.std()) if all_gradients.size else None,
        "custom_edge_sharpness_acutance_3d": _finite(acutance),
        "custom_hsag": _finite(hsag),
        "custom_boundary_convexity": _convexity_by_slice(mask_zyx, spacing_yx),
        "custom_boundary_entropy_ratio": _entropy_ratio(image_zyx, mask_zyx),
        "custom_contour_convexity": contour_convexity,
        "custom_contour_solidity": contour_solidity,
        "custom_contour_circularity": contour_circularity,
        "custom_contour_compactness": contour_compactness,
    }
    result.update(_cdf_features(image_zyx, mask_zyx))
    return {key: float(value) for key, value in result.items() if value is not None and np.isfinite(float(value))}
