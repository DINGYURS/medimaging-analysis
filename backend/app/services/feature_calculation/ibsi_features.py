"""IBSI 2019 reference-manual v11 feature extraction for prepared arrays.

This module is intentionally independent from the database and from the
legacy PyRadiomics service.  It accepts arrays that are already on one grid,
performs IBSI fixed-bin-size or independent family binning, and returns a structured result
containing canonical ``ibsi_<permanent-code>`` values plus an explicit reason
for every value that could not be computed.

The implementation uses merged 3-D (or selected maximum-ROI-slice 2-D)
matrices.  The aggregation method and all non-default parameters are retained
in provenance so callers do not confuse these values with PyRadiomics output.
"""

from __future__ import annotations

import math
from itertools import product
from typing import Any, Callable, Iterable

import numpy as np
from scipy import ndimage
from scipy.integrate import trapezoid
from scipy.spatial import ConvexHull
from skimage.measure import marching_cubes
from .spatial import diameter as exact_diameter, autocorrelation as spatial_autocorrelation, SPATIAL_VERSION

from app.services.feature_calculation.ibsi_feature_catalog import (
    IBSI_EXPECTED_FEATURE_COUNT,
    IBSI_FEATURE_CATALOG,
    IBSI_FEATURES_BY_CODE,
)


IBSI_STANDARD = "IBSI 2019 reference manual arXiv:1612.07003v11"
IBSI_SOURCE = "IBSI reference manual arXiv:1612.07003v11; table 3.1 and sections 3.1-3.11"
# Stable implementation identifier for cache/result hashing.  Bump this when
# formulas, discretisation semantics, or the catalogue changes.
IBSI_ENGINE_VERSION = "ibsi-native-2026.11.14-isolation-v3"
DEFAULT_MAX_PAIRWISE_VOXELS = 4096
LOCAL_PEAK_RADIUS_MM = (3000.0 / (4.0 * math.pi)) ** (1.0 / 3.0)  # 0.62 cm; 1 cm^3 sphere radius


def _feature_formula(evaluate):
    """A scalar formula failure cannot erase other values in the same matrix."""
    try:
        return evaluate()
    except Exception:
        return None


class IBSIInputError(ValueError):
    """Raised when image, mask, spacing, or discretisation arguments are invalid."""


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(value))
    except (TypeError, ValueError):
        return False


def _float(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def _as_bool_array(value: Any, *, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 3:
        raise IBSIInputError(f"{name} must be a 3-D array in image_xyz order")
    return array.astype(bool, copy=False)


def _as_image_array(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 3:
        raise IBSIInputError("image_xyz must be a 3-D array in image_xyz order")
    return array


def _spacing(value: Any) -> tuple[float, float, float]:
    try:
        values = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise IBSIInputError("spacing_xyz must contain three finite positive values") from None
    if len(values) != 3 or not all(math.isfinite(item) and item > 0 for item in values):
        raise IBSIInputError("spacing_xyz must contain three finite positive values")
    return values  # type: ignore[return-value]


def _canonical_dimension(dimension: str | None, max_dimensionality: str | None) -> str:
    value = str(max_dimensionality or dimension or "3d").strip().casefold().replace("-", "").replace("_", "")
    if value in {"3d", "volume", "full"}:
        return "3d"
    if value in {"2d", "maxslice2d", "maxslice", "slice"}:
        return "2d"
    raise IBSIInputError("dimension must be '3d' or '2d'/'maxslice2d'")


def _canonical_aggregation(value: str | None, dimension: str) -> str:
    normalized = str(value or "merged").strip().casefold().replace("-", "_")
    if normalized in {"merged", "full_merged", "merged_3d", "3d_merged", "merged_2d", "2d_merged"}:
        return "merged_3d" if dimension == "3d" else "merged_2d"
    if normalized in {"averaged", "average", "direction_averaged", "averaged_3d", "3d_averaged", "averaged_2d", "2d_averaged"}:
        return "averaged_3d" if dimension == "3d" else "averaged_2d"
    raise IBSIInputError("matrix_aggregation must be a standardized merged or averaged mode")


def _inside(index: tuple[int, ...], shape: tuple[int, ...]) -> bool:
    return all(0 <= value < shape[axis] for axis, value in enumerate(index))


def _safe_percentile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(values, percentile, method="linear"))


def _safe_entropy(probabilities: np.ndarray) -> float:
    p = probabilities[np.isfinite(probabilities) & (probabilities > 0)]
    return float(-np.sum(p * np.log2(p))) if p.size else 0.0


def _squared_weighted_variance(values: np.ndarray, weights: np.ndarray) -> float:
    total = float(np.sum(weights))
    if total <= 0:
        return 0.0
    mean = float(np.sum(values * weights) / total)
    return float(np.sum(((values - mean) ** 2) * weights) / total)


def _offsets(ndim: int, distance: int = 1) -> list[tuple[int, ...]]:
    if distance < 1:
        raise IBSIInputError("texture_distance must be a positive integer")
    # IBSI defines one fixed set of unique direction vectors and scales each
    # vector by the requested grid distance.  Enumerating the whole Chebyshev
    # shell at distance > 1 would incorrectly create 49 directions in 3-D
    # (and 8 in 2-D) instead of the standard 13 and 4.
    candidates = product((-1, 0, 1), repeat=ndim)
    result: list[tuple[int, ...]] = []
    for offset in candidates:
        if not any(offset):
            continue
        # Keep one representative of m and -m.  GLCM construction explicitly
        # adds the transpose, as required by the IBSI symmetric definition.
        first_nonzero = next(item for item in offset if item != 0)
        if first_nonzero > 0:
            result.append(tuple(int(item * distance) for item in offset))
    return result


def _surface_mesh(mask: np.ndarray, spacing: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray, float, float] | None:
    if not np.any(mask):
        return None
    # Padding is required for an ROI touching the array boundary and is also
    # what makes a single voxel a valid closed mesh.
    padded = np.pad(mask.astype(np.float64), 1, mode="constant", constant_values=0)
    try:
        vertices, faces, _normals, _values = marching_cubes(
            padded,
            level=0.5,
            spacing=spacing,
            allow_degenerate=False,
        )
    except (RuntimeError, ValueError):
        return None
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    area = float(np.sum(np.linalg.norm(cross, axis=1) * 0.5))
    signed_volume = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2]))
    volume = float(abs(np.sum(signed_volume)) / 6.0)
    if not (math.isfinite(area) and math.isfinite(volume) and area > 0 and volume > 0):
        return None
    return vertices, faces, area, volume


def _ellipsoid_surface(semi_axes: np.ndarray) -> float | None:
    """IBSI's Legendre-series approximation for an ellipsoid surface."""

    axes = np.sort(np.asarray(semi_axes, dtype=np.float64))[::-1]
    if axes.size != 3 or np.any(~np.isfinite(axes)) or np.any(axes <= 0):
        return None
    a, b, c = (float(item) for item in axes)
    if abs(a - b) < 1e-12 and abs(b - c) < 1e-12:
        return float(4.0 * math.pi * a * a)
    alpha = math.sqrt(max(0.0, 1.0 - (b * b) / (a * a)))
    beta = math.sqrt(max(0.0, 1.0 - (c * c) / (a * a)))
    if alpha <= 1e-12 or beta <= 1e-12:
        return float(4.0 * math.pi * a * a)
    from scipy.special import eval_legendre

    ratio = alpha * beta
    argument = (alpha * alpha + beta * beta) / (2.0 * alpha * beta)
    total = 0.0
    for nu in range(21):
        denominator = 1.0 - 4.0 * nu * nu
        term = (ratio**nu) / denominator * float(eval_legendre(nu, argument))
        total += term
        if nu > 4 and abs(term) < 1e-12:
            break
    area = 4.0 * math.pi * a * b * total
    return float(area) if math.isfinite(area) and area > 0 else None


def _convex_hull(vertices: np.ndarray) -> tuple[float, float, np.ndarray] | None:
    if vertices.shape[0] < 4:
        return None
    try:
        hull = ConvexHull(vertices)
    except (ValueError, RuntimeError):
        return None
    volume = _float(hull.volume)
    area = _float(hull.area)
    if volume is None or area is None or volume <= 0 or area <= 0:
        return None
    return volume, area, vertices[hull.vertices]


def _morphology(
    image: np.ndarray,
    intensity_mask: np.ndarray,
    morphology_mask: np.ndarray,
    spacing: tuple[float, float, float],
    *,
    put: Callable[[str, Any], None],
    unavailable: Callable[[str, str], None],
    max_pairwise_voxels: int | None,
    spatial_method: str = "auto",
    spatial_diagnostics: dict | None = None,
    calculate_diameter: bool = True,
    calculate_mesh: bool = True,
    calculate_autocorrelation: bool = True,
) -> None:
    mesh = _surface_mesh(morphology_mask, spacing) if calculate_mesh else None
    if mesh is None:
        for code in (
            "RNU0", "C0JK", "2PR5", "SKGS", "BQWJ", "KRCK", "QCFX", "25C7",
            "L0JK", "PBX1", "R59B", "6BDE", "RDD2", "R3ER", "7T7F", "99N0",
        ):
            unavailable(code, "a valid closed 3-D ROI mesh could not be generated")
    else:
        vertices, _faces, area, volume = mesh
        put("RNU0", lambda: (volume))
        put("C0JK", lambda: (area))
        put("2PR5", lambda: (area / volume))
        put("SKGS", lambda: (volume / (math.sqrt(math.pi) * area**1.5)))
        compactness2 = 36.0 * math.pi * volume**2 / area**3
        put("BQWJ", lambda: (compactness2))
        # This is deliberately the area-over-sphere-area direction; historic
        # platform names used both directions for similarly named ratios.
        put("KRCK", lambda: (area / (36.0 * math.pi * volume**2) ** (1.0 / 3.0)))
        put("QCFX", lambda: ((36.0 * math.pi * volume**2) ** (1.0 / 3.0) / area))
        put("25C7", lambda: ((area**3 / (36.0 * math.pi * volume**2)) ** (1.0 / 3.0) - 1.0))

        if calculate_diameter:
            try:
                value,diagnostic=exact_diameter(vertices)
                put("L0JK",lambda: (value))
                if spatial_diagnostics is not None:spatial_diagnostics['diameter']=diagnostic
            except Exception as error:
                unavailable("L0JK", "diameter dependency failed: " + type(error).__name__ + ": " + str(error)[:250])

        minimum = np.min(vertices, axis=0)
        maximum = np.max(vertices, axis=0)
        extent = maximum - minimum
        aabb_volume = float(np.prod(extent))
        aabb_area = float(2.0 * (extent[0] * extent[1] + extent[0] * extent[2] + extent[1] * extent[2]))
        if aabb_volume > 0:
            put("PBX1", lambda: (volume / aabb_volume))
        else:
            unavailable("PBX1", "axis-aligned bounding box has zero volume")
        if aabb_area > 0:
            put("R59B", lambda: (area / aabb_area))
        else:
            unavailable("R59B", "axis-aligned bounding box has zero surface area")

        hull = _convex_hull(vertices)
        if hull is None:
            unavailable("R3ER", "physical convex hull is degenerate")
            unavailable("7T7F", "physical convex hull is degenerate")
        else:
            hull_volume, hull_area, _hull_vertices = hull
            put("R3ER", lambda: (volume / hull_volume))
            put("7T7F", lambda: (area / hull_area))

        coords = (np.argwhere(morphology_mask).astype(np.float64) + 0.5) * np.asarray(spacing)
        if coords.shape[0] < 2:
            unavailable("TDIC", "fewer than two morphological voxel centres")
            unavailable("P9VJ", "fewer than two morphological voxel centres")
            unavailable("7J51", "fewer than two morphological voxel centres")
            unavailable("Q3CK", "fewer than two morphological voxel centres")
            unavailable("N17B", "fewer than two morphological voxel centres")
        else:
            # IBSI's PCA reference uses the sample covariance convention
            # (N-1 denominator), which is also numpy's default.  This matters
            # for the small digital phantom and for AEE density values.
            covariance = np.atleast_2d(np.cov(coords, rowvar=False, bias=False))
            eigenvalues = np.sort(np.linalg.eigvalsh(covariance))[::-1]
            eigenvalues = np.maximum(eigenvalues, 0.0)
            major, minor, least = (float(4.0 * math.sqrt(item)) for item in eigenvalues)
            put("TDIC", lambda: (major))
            put("P9VJ", lambda: (minor))
            put("7J51", lambda: (least))
            if eigenvalues[0] > 0:
                put("Q3CK", lambda: (math.sqrt(float(eigenvalues[1] / eigenvalues[0]))))
                put("N17B", lambda: (math.sqrt(float(eigenvalues[2] / eigenvalues[0]))))
            else:
                unavailable("Q3CK", "largest PCA eigenvalue is zero")
                unavailable("N17B", "largest PCA eigenvalue is zero")

            semi_axes = 2.0 * np.sqrt(eigenvalues)
            ellipsoid_volume = float(4.0 * math.pi * np.prod(semi_axes) / 3.0)
            ellipsoid_area = _ellipsoid_surface(semi_axes)
            if ellipsoid_volume > 0:
                put("6BDE", lambda: (volume / ellipsoid_volume))
            else:
                unavailable("6BDE", "PCA approximate enclosing ellipsoid is degenerate")
            if ellipsoid_area is not None:
                put("RDD2", lambda: (area / ellipsoid_area))
            else:
                unavailable("RDD2", "PCA approximate enclosing ellipsoid surface is undefined")

    voxel_volume = float(np.prod(spacing))
    morphology_voxels = int(np.count_nonzero(morphology_mask))
    if morphology_voxels:
        put("YEKZ", lambda: (float(morphology_voxels) * voxel_volume))
    else:
        unavailable("YEKZ", "morphological mask is empty")

    intensity_coords = (np.argwhere(intensity_mask).astype(np.float64) + 0.5) * np.asarray(spacing)
    intensity_values = image[intensity_mask]
    if intensity_values.size == 0:
        unavailable("KLMA", "intensity mask contains no finite voxels")
        unavailable("99N0", "intensity mask contains no finite voxels")
        unavailable("N365", "intensity mask contains no finite voxels")
        unavailable("NPT7", "intensity mask contains no finite voxels")
        return
    if np.any(morphology_mask):
        geometric_coords = (np.argwhere(morphology_mask).astype(np.float64) + 0.5) * np.asarray(spacing)
        geometric_centre = np.mean(geometric_coords, axis=0)
        intensity_sum = float(np.sum(intensity_values))
        if abs(intensity_sum) > 1e-15:
            intensity_centre = np.sum(intensity_coords * intensity_values[:, None], axis=0) / intensity_sum
            put("KLMA", lambda: (np.linalg.norm(geometric_centre - intensity_centre)))
        else:
            unavailable("KLMA", "intensity-weighted centroid denominator is zero")
    if mesh is not None:
        put("99N0", lambda: (volume * float(np.mean(intensity_values))))

    if not calculate_autocorrelation:return
    try:
        moran,geary,diagnostic=spatial_autocorrelation(image,intensity_mask,np.asarray(spacing),method=spatial_method,direct_threshold=max_pairwise_voxels if max_pairwise_voxels is not None else 4096)
        put("N365",lambda: (moran));put("NPT7",lambda: (geary))
        if spatial_diagnostics is not None:spatial_diagnostics['autocorrelation']=diagnostic
    except Exception as error:
        unavailable("N365",str(error));unavailable("NPT7",str(error))


def _continuous_statistics(values: np.ndarray, *, put: Callable[[str, Any], None], unavailable: Callable[[str, str], None]) -> None:
    if values.size == 0:
        for code in ("Q4LE", "ECT3", "KE2A", "IPH6", "Y12H", "1GSF", "QG58", "8DWT", "84IY", "SALO", "2OJQ", "4FUA", "1128", "N72L", "7TET", "9S40", "N8CA", "5ZWQ"):
            unavailable(code, "intensity mask contains no finite voxels")
        return
    mean = float(np.mean(values))
    variance = float(np.var(values))
    centred = values - mean
    put("Q4LE", lambda: (mean))
    put("ECT3", lambda: (variance))
    if variance > 0:
        put("KE2A", lambda: (float(np.mean(centred**3) / variance**1.5)))
        put("IPH6", lambda: (float(np.mean(centred**4) / variance**2 - 3.0)))
    else:
        put("KE2A", lambda: (0.0))
        put("IPH6", lambda: (0.0))
    median = float(np.median(values))
    p10, p25, p75, p90 = (_safe_percentile(values, p) for p in (10, 25, 75, 90))
    minimum, maximum = float(np.min(values)), float(np.max(values))
    put("Y12H", lambda: (median))
    put("1GSF", lambda: (minimum))
    put("QG58", lambda: (p10))
    put("8DWT", lambda: (p90))
    put("84IY", lambda: (maximum))
    put("SALO", lambda: (p75 - p25))
    put("2OJQ", lambda: (maximum - minimum))
    put("4FUA", lambda: (float(np.mean(np.abs(centred)))))
    robust = values[(values >= p10) & (values <= p90)]
    if robust.size:
        robust_centre = float(np.mean(robust))
        put("1128", lambda: (float(np.mean(np.abs(robust - robust_centre)))))
    else:
        unavailable("1128", "10th-90th percentile interval contains no voxels")
    put("N72L", lambda: (float(np.mean(np.abs(values - median)))))
    if abs(mean) > 1e-15:
        put("7TET", lambda: (math.sqrt(variance) / mean))
    else:
        unavailable("7TET", "intensity mean is zero")
    if abs(p75 + p25) > 1e-15:
        put("9S40", lambda: ((p75 - p25) / (p75 + p25)))
    else:
        unavailable("9S40", "quartile coefficient denominator is zero")
    put("N8CA", lambda: (float(np.sum(values**2))))
    put("5ZWQ", lambda: (float(math.sqrt(np.mean(values**2)))))


def _histogram_statistics(q_values: np.ndarray, *, put: Callable[[str, Any], None], unavailable: Callable[[str, str], None]) -> tuple[np.ndarray, np.ndarray]:
    if q_values.size == 0:
        for code in ("X6K6", "CH89", "88K1", "C3I7", "WIFQ", "1PR8", "GPMT", "OZ0C", "3NCY", "AMMC", "WR0O", "5Z3W", "D2ZX", "WRZB", "4RNL", "CWYJ", "SLWD", "TLU2", "BJ5W", "12CE", "8E6O", "VQB3", "RHQZ"):
            unavailable(code, "intensity mask contains no finite voxels")
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    values = q_values.astype(np.float64, copy=False)
    mean = float(np.mean(values))
    variance = float(np.var(values))
    centred = values - mean
    p10, p25, p75, p90 = (_safe_percentile(values, p) for p in (10, 25, 75, 90))
    minimum, maximum = float(np.min(values)), float(np.max(values))
    put("X6K6", lambda: (mean))
    put("CH89", lambda: (variance))
    if variance > 0:
        put("88K1", lambda: (float(np.mean(centred**3) / variance**1.5)))
        put("C3I7", lambda: (float(np.mean(centred**4) / variance**2 - 3.0)))
    else:
        put("88K1", lambda: (0.0))
        put("C3I7", lambda: (0.0))
    put("WIFQ", lambda: (float(np.median(values))))
    put("1PR8", lambda: (minimum))
    put("GPMT", lambda: (p10))
    put("OZ0C", lambda: (p90))
    put("3NCY", lambda: (maximum))
    counts = np.bincount(q_values.astype(np.int64), minlength=int(maximum) + 1)
    levels = np.arange(1, int(maximum) + 1, dtype=np.int64)
    dense_counts = counts[1:]
    max_count = int(np.max(dense_counts))
    tied = levels[dense_counts == max_count]
    mode = int(tied[np.argmin(np.abs(tied.astype(np.float64) - mean))])
    put("AMMC", lambda: (mode))
    put("WR0O", lambda: (p75 - p25))
    put("5Z3W", lambda: (maximum - minimum))
    put("D2ZX", lambda: (float(np.mean(np.abs(centred)))))
    robust = values[(values >= p10) & (values <= p90)]
    if robust.size:
        put("WRZB", lambda: (float(np.mean(np.abs(robust - np.mean(robust))))))
    else:
        unavailable("WRZB", "10th-90th percentile interval contains no discretised voxels")
    median = float(np.median(values))
    put("4RNL", lambda: (float(np.mean(np.abs(values - median)))))
    if abs(mean) > 1e-15:
        put("CWYJ", lambda: (math.sqrt(variance) / mean))
    else:
        unavailable("CWYJ", "discretised intensity mean is zero")
    if abs(p75 + p25) > 1e-15:
        put("SLWD", lambda: ((p75 - p25) / (p75 + p25)))
    else:
        unavailable("SLWD", "discretised quartile coefficient denominator is zero")
    probabilities = dense_counts.astype(np.float64) / q_values.size
    put("TLU2", lambda: (_safe_entropy(probabilities)))
    put("BJ5W", lambda: (float(np.sum(probabilities**2))))
    if dense_counts.size:
        gradient = np.empty_like(dense_counts, dtype=np.float64)
        if dense_counts.size == 1:
            gradient[0] = 0.0
        else:
            gradient[0] = float(dense_counts[1] - dense_counts[0])
            gradient[-1] = float(dense_counts[-1] - dense_counts[-2])
            if dense_counts.size > 2:
                gradient[1:-1] = (dense_counts[2:] - dense_counts[:-2]) / 2.0
        max_index = int(np.argmax(gradient))
        min_index = int(np.argmin(gradient))
        put("12CE", lambda: (float(gradient[max_index])))
        put("8E6O", lambda: (float(levels[max_index])))
        put("VQB3", lambda: (float(gradient[min_index])))
        put("RHQZ", lambda: (float(levels[min_index])))
    return q_values.astype(np.int64, copy=False), dense_counts.astype(np.int64, copy=False)


def _ivh(
    q_values: np.ndarray,
    *,
    level_centres: np.ndarray | None = None,
    level_origin: float | None = None,
    level_width: float | None = None,
    put: Callable[[str, Any], None],
    unavailable: Callable[[str, str], None],
) -> None:
    """Compute IVH values while retaining calibrated intensity units.

    ``q_values`` are integer bin ids.  ``level_centres`` is used for
    calibrated discrete images (for example CT HU), while ``level_origin``
    and ``level_width`` describe the physical centres of fixed-bin-size IVH
    bins.  The old implementation returned bin ids for ``GBPN`` and
    ``CNV2``; that is only correct when the bin width is one in the image's
    physical unit.
    """
    if q_values.size == 0:
        for code in ("BC2M", "GBPN", "DDTU", "CNV2", "9CMM"):
            unavailable(code, "intensity mask contains no finite voxels")
        return
    minimum, maximum = int(np.min(q_values)), int(np.max(q_values))
    levels = np.arange(minimum, maximum + 1, dtype=np.int64)
    if level_centres is not None:
        centres = np.asarray(level_centres, dtype=np.float64)
        start = minimum - 1
        stop = maximum
        if start < 0 or stop > centres.size:
            for code in ("BC2M", "GBPN", "DDTU", "CNV2", "9CMM"):
                unavailable(code, "IVH physical level mapping is incomplete")
            return
        physical_levels = centres[start:stop]
    elif level_origin is not None and level_width is not None:
        physical_levels = float(level_origin) + (levels.astype(np.float64) - 0.5) * float(level_width)
    else:
        physical_levels = levels.astype(np.float64)
    if levels.size < 2:
        unavailable("BC2M", "IVH requires at least two discretised intensity levels")
        unavailable("GBPN", "IVH requires at least two discretised intensity levels")
        unavailable("DDTU", "IVH requires at least two discretised intensity levels")
        unavailable("CNV2", "IVH requires at least two discretised intensity levels")
        unavailable("9CMM", "not standardized by IBSI; IVH has one discretised level")
        return
    volume_fraction = np.asarray([np.mean(q_values >= level) for level in levels], dtype=np.float64)
    physical_range = float(physical_levels[-1] - physical_levels[0])
    if physical_range <= 0 or not math.isfinite(physical_range):
        unavailable("BC2M", "IVH physical intensity range is zero")
        unavailable("GBPN", "IVH physical intensity range is zero")
        unavailable("DDTU", "IVH physical intensity range is zero")
        unavailable("CNV2", "IVH physical intensity range is zero")
        unavailable("9CMM", "not standardized by IBSI; IVH physical intensity range is zero")
        return
    intensity_fraction = (physical_levels - float(physical_levels[0])) / physical_range

    def volume_at_intensity_fraction(fraction: float) -> float:
        index = np.flatnonzero(intensity_fraction >= fraction)
        return float(volume_fraction[index[0]]) if index.size else 0.0

    def intensity_at_volume_fraction(fraction: float) -> float:
        index = np.flatnonzero(volume_fraction <= fraction)
        return float(physical_levels[index[0]]) if index.size else float(physical_levels[-1])

    v10 = volume_at_intensity_fraction(0.10)
    v90 = volume_at_intensity_fraction(0.90)
    i10 = intensity_at_volume_fraction(0.10)
    i90 = intensity_at_volume_fraction(0.90)
    put("BC2M", lambda: (v10))
    put("GBPN", lambda: (i10))
    put("DDTU", lambda: (v10 - v90))
    put("CNV2", lambda: (i10 - i90))
    put("9CMM", lambda: (float(trapezoid(volume_fraction, intensity_fraction))))


def _glcm(q: np.ndarray, mask: np.ndarray, *, distance: int) -> np.ndarray | None:
    if not np.any(mask):
        return None
    levels = int(np.max(q[mask]))
    matrix = np.zeros((levels, levels), dtype=np.float64)
    coordinates = np.argwhere(mask)
    shape = mask.shape
    for offset in _offsets(mask.ndim, distance):
        for coordinate in coordinates:
            neighbour = tuple(int(coordinate[axis] + offset[axis]) for axis in range(mask.ndim))
            current = tuple(int(item) for item in coordinate)
            if _inside(neighbour, shape) and mask[neighbour]:
                left = int(q[current]) - 1
                right = int(q[neighbour]) - 1
                matrix[left, right] += 1.0
                matrix[right, left] += 1.0
    return matrix if float(np.sum(matrix)) > 0 else None


def _glcm_features(matrix: np.ndarray) -> dict[str, float | None]:
    codes = (
        "GYBY", "60VM", "UR99", "TU9B", "TF7R", "D3YU", "NTRS", "ZGXS", "OEEB", "P6QZ",
        "8ZQL", "ACUI", "8S9J", "IB1Z", "NDRX", "WF0Z", "1QCO", "E8JP", "NI2N", "QWB0",
        "DG8W", "7NFM", "AE86", "R8DG", "JN9H",
    )
    total = float(np.sum(matrix))
    if total <= 0:
        return {code: None for code in codes}
    p = matrix / total
    levels = np.arange(1, matrix.shape[0] + 1, dtype=np.float64)
    i, j = np.meshgrid(levels, levels, indexing="ij")
    px = np.sum(p, axis=1)
    mean = float(np.sum(levels * px))
    variance = float(np.sum(((levels - mean) ** 2) * px))
    abs_difference = np.abs(i - j)
    difference = np.bincount(abs_difference.astype(np.int64).ravel(), weights=p.ravel(), minlength=matrix.shape[0])
    sums = (i + j).astype(np.int64)
    sum_distribution = np.bincount(sums.ravel(), weights=p.ravel(), minlength=2 * matrix.shape[0] + 1)
    diff_levels = np.arange(difference.size, dtype=np.float64)
    sum_levels = np.arange(sum_distribution.size, dtype=np.float64)
    diff_mean = float(np.sum(diff_levels * difference))
    sum_mean = float(np.sum(sum_levels * sum_distribution))
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse_variance = float(np.sum(np.where(abs_difference > 0, p / (abs_difference**2), 0.0)))
        correlation = float(np.sum((i - mean) * (j - mean) * p) / variance) if variance > 0 else None
    hxy = _feature_formula(lambda: _safe_entropy(p.ravel()))
    hx = _feature_formula(lambda: _safe_entropy(px))
    pxpy = px[:, None] * px[None, :]
    nonzero = p > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        hxy1 = float(-np.sum(p[nonzero] * np.log2(pxpy[nonzero]))) if np.any(nonzero) else 0.0
    positive_products = pxpy > 0
    hxy2 = float(-np.sum(pxpy[positive_products] * np.log2(pxpy[positive_products]))) if np.any(positive_products) else 0.0
    norm = max(1.0, float(matrix.shape[0]))
    return {
        "GYBY": _feature_formula(lambda: (float(np.max(p)))),
        "60VM": _feature_formula(lambda: (mean)),
        "UR99": _feature_formula(lambda: (float(np.sum((i - mean) ** 2 * p)))),
        "TU9B": _feature_formula(lambda: (hxy)),
        "TF7R": _feature_formula(lambda: (diff_mean)),
        "D3YU": _feature_formula(lambda: (float(np.sum((diff_levels - diff_mean) ** 2 * difference)))),
        "NTRS": _feature_formula(lambda: (_safe_entropy(difference))),
        "ZGXS": _feature_formula(lambda: (sum_mean)),
        "OEEB": _feature_formula(lambda: (float(np.sum((sum_levels - sum_mean) ** 2 * sum_distribution)))),
        "P6QZ": _feature_formula(lambda: (_safe_entropy(sum_distribution))),
        "8ZQL": _feature_formula(lambda: (float(np.sum(p**2)))),
        "ACUI": _feature_formula(lambda: (float(np.sum(abs_difference**2 * p)))),
        "8S9J": _feature_formula(lambda: (float(np.sum(abs_difference * p)))),
        "IB1Z": _feature_formula(lambda: (float(np.sum(p / (1.0 + abs_difference))))),
        "NDRX": _feature_formula(lambda: (float(np.sum(p / (1.0 + abs_difference / norm))))),
        "WF0Z": _feature_formula(lambda: (float(np.sum(p / (1.0 + abs_difference**2))))),
        "1QCO": _feature_formula(lambda: (float(np.sum(p / (1.0 + (abs_difference / norm) ** 2))))),
        "E8JP": _feature_formula(lambda: (inverse_variance)),
        "NI2N": _feature_formula(lambda: (correlation)),
        "QWB0": _feature_formula(lambda: (float(np.sum(i * j * p)))),
        "DG8W": _feature_formula(lambda: (float(np.sum((i + j - 2.0 * mean) ** 2 * p)))),
        "7NFM": _feature_formula(lambda: (float(np.sum((i + j - 2.0 * mean) ** 3 * p)))),
        "AE86": _feature_formula(lambda: (float(np.sum((i + j - 2.0 * mean) ** 4 * p)))),
        "R8DG": _feature_formula(lambda: (float((hxy - hxy1) / hx) if hx > 0 else None)),
        "JN9H": _feature_formula(lambda: (float(math.sqrt(max(0.0, 1.0 - math.exp(-2.0 * (hxy2 - hxy))))))),
    }


def _run_matrix(q: np.ndarray, mask: np.ndarray, *, distance: int) -> tuple[np.ndarray | None, int]:
    if not np.any(mask):
        return None, 0
    levels = int(np.max(q[mask]))
    counts: dict[tuple[int, int], float] = {}
    coordinates = np.argwhere(mask)
    shape = mask.shape
    direction_count = 0
    for offset in _offsets(mask.ndim, distance):
        direction_count += 1
        for coordinate in coordinates:
            current = tuple(int(item) for item in coordinate)
            previous = tuple(int(coordinate[axis] - offset[axis]) for axis in range(mask.ndim))
            if _inside(previous, shape) and mask[previous] and int(q[previous]) == int(q[tuple(int(item) for item in coordinate)]):
                continue
            level = int(q[current]) - 1
            run_length = 0
            cursor = current
            while _inside(cursor, shape) and mask[cursor] and int(q[cursor]) - 1 == level:
                run_length += 1
                cursor = tuple(cursor[axis] + offset[axis] for axis in range(mask.ndim))
            if run_length:
                key = (level, run_length - 1)
                counts[key] = counts.get(key, 0.0) + 1.0
    if not counts:
        return None, direction_count
    last = max(column for _row, column in counts) + 1
    matrix = np.zeros((levels, last), dtype=np.float64)
    for (row, column), value in counts.items():
        matrix[row, column] = value
    return matrix, direction_count


def _zone_matrix(q: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    if not np.any(mask):
        return None
    levels = int(np.max(q[mask]))
    counts: dict[tuple[int, int], float] = {}
    structure = np.ones((3,) * mask.ndim, dtype=np.uint8)
    for level in range(1, levels + 1):
        labels, count = ndimage.label(mask & (q == level), structure=structure)
        if count == 0:
            continue
        sizes = np.bincount(labels.ravel())[1:]
        for size, occurrences in enumerate(np.bincount(sizes), start=0):
            if size > 0 and occurrences:
                counts[(level - 1, size - 1)] = float(occurrences)
    if not counts:
        return None
    last = max(column for _row, column in counts) + 1
    matrix = np.zeros((levels, last), dtype=np.float64)
    for (row, column), value in counts.items():
        matrix[row, column] = value
    return matrix


def _gldzm_matrix(q: np.ndarray, intensity_mask: np.ndarray, morphology_mask: np.ndarray) -> np.ndarray | None:
    active = intensity_mask & morphology_mask
    if not np.any(active):
        return None
    # Padding supplies an explicit outside-of-array background.  Without it,
    # scipy returns -1 for a mask that fills the complete prepared volume,
    # whereas IBSI defines the boundary distance of an edge voxel as one.
    padded_morphology = np.pad(morphology_mask, 1, mode="constant", constant_values=False)
    distance_map = ndimage.distance_transform_cdt(padded_morphology, metric="taxicab")[
        tuple(slice(1, -1) for _ in range(morphology_mask.ndim))
    ]
    levels = int(np.max(q[active]))
    counts: dict[tuple[int, int], float] = {}
    structure = np.ones((3,) * active.ndim, dtype=np.uint8)
    for level in range(1, levels + 1):
        labels, count = ndimage.label(active & (q == level), structure=structure)
        if count == 0:
            continue
        for label in range(1, count + 1):
            distance = int(np.min(distance_map[labels == label]))
            if distance > 0:
                key = (level - 1, distance - 1)
                counts[key] = counts.get(key, 0.0) + 1.0
    if not counts:
        return None
    last = max(column for _row, column in counts) + 1
    matrix = np.zeros((levels, last), dtype=np.float64)
    for (row, column), value in counts.items():
        matrix[row, column] = value
    return matrix


def _ngldm_matrix(q: np.ndarray, mask: np.ndarray, *, alpha: int, delta: int) -> np.ndarray | None:
    if not np.any(mask):
        return None
    levels = int(np.max(q[mask]))
    matrix = np.zeros((levels, int((2 * delta + 1) ** mask.ndim)), dtype=np.float64)
    coordinates = np.argwhere(mask)
    shape = mask.shape
    offsets = [offset for offset in product(range(-delta, delta + 1), repeat=mask.ndim) if any(offset)]
    for coordinate in coordinates:
        current = tuple(int(item) for item in coordinate)
        current_level = int(q[current])
        dependence = 1
        for offset in offsets:
            neighbour = tuple(current[axis] + offset[axis] for axis in range(mask.ndim))
            if _inside(neighbour, shape) and mask[neighbour] and abs(int(q[neighbour]) - current_level) <= alpha:
                dependence += 1
        matrix[current_level - 1, dependence - 1] += 1.0
    last = int(np.max(np.flatnonzero(np.any(matrix > 0, axis=0)))) + 1 if np.any(matrix) else 0
    return matrix[:, :last] if last else None


def _run_like_features(matrix: np.ndarray | None, *, codes: tuple[str, ...], denominator: float, col_name: str) -> dict[str, float | None]:
    if matrix is None:
        return {code: None for code in codes}
    total = float(np.sum(matrix))
    if total <= 0:
        return {code: None for code in codes}
    rows = np.arange(1, matrix.shape[0] + 1, dtype=np.float64)
    cols = np.arange(1, matrix.shape[1] + 1, dtype=np.float64)
    i, j = np.meshgrid(rows, cols, indexing="ij")
    row_sum = np.sum(matrix, axis=1)
    col_sum = np.sum(matrix, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        values = [
            _feature_formula(lambda: (np.sum(matrix / j**2) / total)),
            _feature_formula(lambda: (np.sum(matrix * j**2) / total)),
            _feature_formula(lambda: (np.sum(matrix / i**2) / total)),
            _feature_formula(lambda: (np.sum(matrix * i**2) / total)),
            _feature_formula(lambda: (np.sum(matrix / (i**2 * j**2)) / total)),
            _feature_formula(lambda: (np.sum(matrix * i**2 / j**2) / total)),
            _feature_formula(lambda: (np.sum(matrix * j**2 / i**2) / total)),
            _feature_formula(lambda: (np.sum(matrix * i**2 * j**2) / total)),
            _feature_formula(lambda: (np.sum(row_sum**2) / total)),
            _feature_formula(lambda: (np.sum(row_sum**2) / total**2)),
            _feature_formula(lambda: (np.sum(col_sum**2) / total)),
            _feature_formula(lambda: (np.sum(col_sum**2) / total**2)),
            _feature_formula(lambda: (total / denominator if denominator > 0 else None)),
            _feature_formula(lambda: (_squared_weighted_variance(rows, row_sum))),
            _feature_formula(lambda: (_squared_weighted_variance(cols, col_sum))),
            _feature_formula(lambda: (_safe_entropy((matrix / total).ravel()))),
        ]
    return {code: _float(value) for code, value in zip(codes, values)}


def _ngtdm_features(q: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    codes = ("QCDE", "65HE", "NQ30", "HDEZ", "1X9X")
    levels = int(np.max(q[mask])) if np.any(mask) else 0
    if levels <= 0:
        return {code: None for code in codes}
    counts = np.zeros(levels, dtype=np.float64)
    differences = np.zeros(levels, dtype=np.float64)
    offsets = [offset for offset in product((-1, 0, 1), repeat=mask.ndim) if any(offset)]
    coordinates = np.argwhere(mask)
    for coordinate in coordinates:
        current = tuple(int(item) for item in coordinate)
        neighbours = []
        for offset in offsets:
            neighbour = tuple(current[axis] + offset[axis] for axis in range(mask.ndim))
            if _inside(neighbour, mask.shape) and mask[neighbour]:
                neighbours.append(float(q[neighbour]))
        if not neighbours:
            continue
        level = int(q[current]) - 1
        counts[level] += 1.0
        differences[level] += abs(float(q[current]) - float(np.mean(neighbours)))
    valid = float(np.sum(counts))
    if valid <= 0:
        return {code: None for code in codes}
    p = counts / valid
    levels_array = np.arange(1, levels + 1, dtype=np.float64)
    weighted_difference = float(np.sum(p * differences))
    coarseness = None if weighted_difference <= 0 else 1.0 / weighted_difference
    present = p > 0
    present_count = int(np.count_nonzero(present))
    if present_count > 1:
        pairwise = (levels_array[:, None] - levels_array[None, :]) ** 2
        first_term = float(np.sum(p[:, None] * p[None, :] * pairwise) / (present_count * (present_count - 1)))
    else:
        first_term = 0.0
    second_term = float(np.sum(differences) / valid)
    # IBSI excludes grey levels with zero probability from both sums.  Keeping
    # their empty bins in the denominator would bias busyness downward when a
    # fixed-width discretisation has gaps (the digital phantom intentionally
    # omits grey levels 2 and 5).
    present_levels = levels_array[present]
    present_probabilities = p[present]
    denominator = float(
        np.sum(
            np.abs(
                present_levels[:, None] * present_probabilities[:, None]
                - present_levels[None, :] * present_probabilities[None, :]
            )
        )
    )
    busyness = weighted_difference / denominator if denominator > 0 and present_count > 1 else None
    complexity = 0.0
    strength_numerator = 0.0
    for first in range(levels):
        for second in range(levels):
            if not (present[first] and present[second]):
                continue
            difference = abs(float(first - second))
            complexity += difference * (p[first] * differences[first] + p[second] * differences[second]) / max(p[first] + p[second], 1e-15)
            strength_numerator += (p[first] + p[second]) * difference**2
    difference_sum = float(np.sum(differences))
    strength = strength_numerator / difference_sum if difference_sum > 0 else None
    return {
        "QCDE": _feature_formula(lambda: (coarseness)),
        "65HE": _feature_formula(lambda: (first_term * second_term)),
        "NQ30": _feature_formula(lambda: (busyness)),
        "HDEZ": _feature_formula(lambda: (complexity / valid)),
        "1X9X": _feature_formula(lambda: (strength)),
    }


def _spherical_mean_map(image: np.ndarray, spacing: tuple[float, float, float]) -> np.ndarray:
    """Return 1 cm³ spherical means for every finite image voxel.

    A single convolution is materially more practical than constructing a
    full coordinate grid for every ROI centre.  The valid-voxel convolution
    keeps image-boundary neighbourhoods well-defined and excludes NaNs
    without manufacturing intensity values.
    """

    radius_voxels = tuple(int(math.ceil(LOCAL_PEAK_RADIUS_MM / value)) for value in spacing)
    axes = [np.arange(-radius, radius + 1, dtype=np.float64) * spacing[axis] for axis, radius in enumerate(radius_voxels)]
    grid = np.meshgrid(*axes, indexing="ij")
    kernel = (np.sqrt(np.sum(np.stack([axis**2 for axis in grid], axis=0), axis=0)) <= LOCAL_PEAK_RADIUS_MM).astype(np.float64)
    finite = np.isfinite(image)
    safe_image = np.where(finite, image, 0.0)
    summed = ndimage.convolve(safe_image, kernel, mode="constant", cval=0.0)
    counts = ndimage.convolve(finite.astype(np.float64), kernel, mode="constant", cval=0.0)
    means = np.full(image.shape, np.nan, dtype=np.float64)
    np.divide(summed, counts, out=means, where=counts > 0)
    return means


def _minimal_crop(
    image: np.ndarray,
    intensity_mask: np.ndarray,
    morphology_mask: np.ndarray,
    spacing: tuple[float, ...],
    *,
    include_local_context: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[slice, ...], tuple[int, ...], tuple[int, ...]]:
    """Crop arrays to the mask union plus the local-peak context.

    A clinical image can contain hundreds of millions of voxels while the
    ROI occupies a small region.  Local intensity features need only a
    6.20 mm radius (the radius of a 1 cm^3 sphere) around ROI centres.  The
    returned crop therefore bounds the convolution and all Python texture
    loops without changing physical distances or values.
    """

    shape = image.shape
    active = intensity_mask | morphology_mask
    # Avoid materialising one coordinate row per active voxel.  On a large
    # prepared scan that temporary ``argwhere`` array can itself be larger
    # than the cropped feature input.  Three axis projections provide the
    # same bounds in constant memory with a tiny fixed amount of Python work.
    if not np.any(active):
        lows = np.zeros(len(shape), dtype=np.int64)
        highs = np.ones(len(shape), dtype=np.int64)
        context = tuple(0 for _ in shape)
    else:
        if include_local_context:
            context = tuple(int(math.ceil(LOCAL_PEAK_RADIUS_MM / value)) + 1 for value in spacing)
        else:
            context = tuple(0 for _ in shape)
        lower_bounds: list[int] = []
        upper_bounds: list[int] = []
        for axis in range(active.ndim):
            other_axes = tuple(index for index in range(active.ndim) if index != axis)
            projection = np.any(active, axis=other_axes)
            indices = np.flatnonzero(projection)
            # ``active`` was non-empty, so every axis projection must have at
            # least one index.  Keep the guard for malformed ndarray-like
            # inputs and to avoid constructing an invalid slice on failure.
            if indices.size == 0:
                raise IBSIInputError("mask bounds could not be determined")
            lower_bounds.append(int(indices[0]))
            upper_bounds.append(int(indices[-1]) + 1)
        lows = np.maximum(np.asarray(lower_bounds, dtype=np.int64) - np.asarray(context), 0)
        highs = np.minimum(np.asarray(upper_bounds, dtype=np.int64) + np.asarray(context), np.asarray(shape))
    slices = tuple(slice(int(low), int(high)) for low, high in zip(lows, highs))
    origin = tuple(int(value) for value in lows)
    return image[slices], intensity_mask[slices], morphology_mask[slices], slices, origin, context


def _texture(
    q: np.ndarray,
    intensity_mask: np.ndarray,
    morphology_mask: np.ndarray,
    *,
    families: set[str] | None = None,
    distance: int,
    ngldm_alpha: int,
    ngldm_delta: int,
    put: Callable[[str, Any], None],
    unavailable: Callable[[str, str], None],
) -> None:
    voxel_count = float(np.count_nonzero(intensity_mask))
    if families is None or "glcm" in families:
        glcm = _glcm(q, intensity_mask, distance=distance)
        if glcm is None:
            for code in tuple(spec.code for spec in IBSI_FEATURE_CATALOG if spec.family == "glcm"):
                unavailable(code, "no valid same-mask neighbour pair for the requested texture distance")
        else:
            for code, value in _glcm_features(glcm).items():
                if value is None:
                    unavailable(code, "GLCM definition is undefined for this constant or degenerate matrix")
                else:
                    put(code, lambda: (value))

    if families is None or "glrlm" in families:
        glrlm_codes = tuple(spec.code for spec in IBSI_FEATURE_CATALOG if spec.family == "glrlm")
        glrlm, directions = _run_matrix(q, intensity_mask, distance=distance)
        for code, value in _run_like_features(glrlm, codes=glrlm_codes, denominator=max(1.0, voxel_count * directions), col_name="run length").items():
            if value is None:
                unavailable(code, "no valid grey-level runs")
            else:
                put(code, lambda: (value))

    if families is None or "glszm" in families:
        glszm_codes = tuple(spec.code for spec in IBSI_FEATURE_CATALOG if spec.family == "glszm")
        glszm = _zone_matrix(q, intensity_mask)
        for code, value in _run_like_features(glszm, codes=glszm_codes, denominator=max(1.0, voxel_count), col_name="zone size").items():
            if value is None:
                unavailable(code, "no valid grey-level zones")
            else:
                put(code, lambda: (value))

    if families is None or "gldzm" in families:
        gldzm_codes = tuple(spec.code for spec in IBSI_FEATURE_CATALOG if spec.family == "gldzm")
        gldzm = _gldzm_matrix(q, intensity_mask, morphology_mask)
        for code, value in _run_like_features(gldzm, codes=gldzm_codes, denominator=max(1.0, voxel_count), col_name="zone distance").items():
            if value is None:
                unavailable(code, "no valid grey-level distance zones")
            else:
                put(code, lambda: (value))

    if families is None or "ngldm" in families:
        ngldm_codes = tuple(spec.code for spec in IBSI_FEATURE_CATALOG if spec.family == "ngldm")
        ngldm = _ngldm_matrix(q, intensity_mask, alpha=ngldm_alpha, delta=ngldm_delta)
        ngldm_values = _run_like_features(ngldm, codes=ngldm_codes[:16], denominator=max(1.0, voxel_count), col_name="dependence count")
        # NGLDM has one additional energy feature after the sixteen run-like
        # definitions; compute it from the same normalized matrix.
        if ngldm is not None and np.sum(ngldm) > 0:
            ngldm_values["CAS9"] = float(np.sum((ngldm / np.sum(ngldm)) ** 2))
        else:
            ngldm_values["CAS9"] = None
        for code, value in ngldm_values.items():
            if value is None:
                unavailable(code, "no valid neighbouring grey-level dependences")
            else:
                put(code, lambda: (value))

    if families is None or "ngtdm" in families:
        for code, value in _ngtdm_features(q, intensity_mask).items():
            if value is None:
                unavailable(code, "no voxel has a valid intensity-mask neighbourhood")
            else:
                put(code, lambda: (value))


def _canonical_catalog_result(features: dict[str, float], unavailable: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for spec in IBSI_FEATURE_CATALOG:
        key = spec.key
        row = spec.as_dict()
        if spec.standard_status == "not_standardized":
            runtime_status = "not_standardized"
        elif key in features:
            runtime_status = "computed"
        else:
            runtime_status = "unavailable"
        row["runtime_status"] = runtime_status
        row["status"] = runtime_status
        if key in unavailable:
            row["unavailable_reason"] = unavailable[key]
            row["reason"] = unavailable[key]
        result.append(row)
    return result


def compute_ibsi_features(
    image_xyz: Any,
    mask_xyz: Any,
    spacing_xyz: Iterable[float],
    *,
    selected_keys: list[str] | None = None,
    bin_width: float = 1.0,
    modality: str = "generic",
    dimension: str = "3d",
    max_dimensionality: str | None = None,
    bin_origin: float | None = None,
    morphology_mask_xyz: Any | None = None,
    intensity_mask_xyz: Any | None = None,
    ivh_bin_width: float | None = None,
    discretisation: dict[str, Any] | None = None,
    texture_distance: int = 1,
    matrix_aggregation: str = "merged",
    ngldm_alpha: int = 0,
    ngldm_delta: int = 1,
    max_pairwise_voxels: int | None = DEFAULT_MAX_PAIRWISE_VOXELS,
    spatial_method: str = "auto",
) -> dict[str, Any]:
    """Compute IBSI features from arrays on a common prepared grid.

    Parameters follow IBSI's terminology.  ``bin_width`` is fixed-bin-size
    discretisation and ``bin_origin`` is the declared lower intensity bound;
    when omitted, the minimum finite intensity in the intensity mask is used
    and is retained in provenance.  ``matrix_aggregation='merged'`` (or
    ``'3d_merged'``/``'2d_merged'``) selects the implemented standardized
    merged mode.  Standardized averaged aliases are accepted and reported as
    unavailable until direction averaging is explicitly requested.  IVH
    discretisation is independent from texture binning: CT and the checked-in
    digital phantom use calibrated unit-spaced intensities by default, while
    other modalities use ``ivh_bin_width`` when supplied or the texture bin
    width as a documented fallback.
    ``discretisation`` opts into independent IH/texture FBS or FBN and IVH
    auto/FBS rules. FBN uses the final intensity-mask range; a constant range
    marks only that group unavailable. Legacy calls retain their old rules.
    ``dimension='2d'`` selects the axial slice with the largest ROI area for
    intensity/statistical/texture families and marks all 3-D morphology and
    local-peak values unavailable, as IBSI morphology and local intensity are
    defined on a volume.
    """

    selected = None
    if selected_keys is not None:
        from app.services.feature_calculation.ibsi_feature_catalog import get_ibsi_feature_spec
        if not isinstance(selected_keys, list) or not selected_keys or any(not isinstance(key, str) for key in selected_keys):
            raise IBSIInputError("selected_keys must be a non-empty list of canonical IBSI keys")
        if len(set(selected_keys)) != len(selected_keys):
            raise IBSIInputError("selected_keys must not contain duplicates")
        for key in selected_keys:
            try:
                spec = get_ibsi_feature_spec(key)
            except KeyError:
                raise IBSIInputError(f"unknown selected feature: {key}") from None
            if spec.key != key or spec.standard_status != "computed":
                raise IBSIInputError(f"unsupported selected feature: {key}")
        selected = set(selected_keys)

    families = {spec.family for spec in IBSI_FEATURE_CATALOG if selected is None or spec.key in selected}
    family_rules = None
    if discretisation is not None:
        from app.services.feature_calculation.parameters import PlanDiscretisation
        family_rules = PlanDiscretisation.model_validate(discretisation).model_dump()
        for group, group_families in {
            "intensity_histogram": {"intensity_histogram"},
            "texture": {"glcm", "glrlm", "glszm", "gldzm", "ngtdm", "ngldm"},
        }.items():
            if families.intersection(group_families) and family_rules[group]["method"] == "none":
                raise IBSIInputError(f"{group} requires discretisation")

    image = _as_image_array(image_xyz)
    base_mask = _as_bool_array(mask_xyz, name="mask_xyz")
    if image.shape != base_mask.shape:
        raise IBSIInputError("image_xyz and mask_xyz must have identical shapes")
    spacing = _spacing(spacing_xyz)
    dimension_name = _canonical_dimension(dimension, max_dimensionality)
    aggregation_name = _canonical_aggregation(matrix_aggregation, dimension_name)
    try:
        width = float(bin_width)
    except (TypeError, ValueError):
        raise IBSIInputError("bin_width must be finite and positive") from None
    if not math.isfinite(width) or width <= 0:
        raise IBSIInputError("bin_width must be finite and positive")
    modality_token = str(modality).strip().casefold().replace("-", "").replace("_", "").replace(" ", "")
    discrete_ivh = (
        modality_token.startswith("ct")
        or modality_token.startswith("computedtomography")
        or modality_token == "digitalphantom"
    )
    if ivh_bin_width is not None:
        try:
            ivh_width = float(ivh_bin_width)
        except (TypeError, ValueError):
            raise IBSIInputError("ivh_bin_width must be finite and positive") from None
        if not math.isfinite(ivh_width) or ivh_width <= 0:
            raise IBSIInputError("ivh_bin_width must be finite and positive")
        ivh_mode = "fixed_bin_size"
    elif discrete_ivh:
        ivh_width = 1.0
        ivh_mode = "discrete_calibrated"
    else:
        ivh_width = width
        ivh_mode = "fixed_bin_size"
    declared_origin: float | None = None
    if bin_origin is not None:
        try:
            declared_origin = float(bin_origin)
        except (TypeError, ValueError):
            raise IBSIInputError("bin_origin must be finite when supplied") from None
        if not math.isfinite(declared_origin):
            raise IBSIInputError("bin_origin must be finite when supplied")
    try:
        distance = int(texture_distance)
        alpha = int(ngldm_alpha)
        delta = int(ngldm_delta)
    except (TypeError, ValueError):
        raise IBSIInputError("texture_distance, ngldm_alpha, and ngldm_delta must be integers") from None
    if distance < 1 or alpha < 0 or delta < 1:
        raise IBSIInputError("texture_distance and ngldm_delta must be positive; ngldm_alpha must be non-negative")
    if max_pairwise_voxels is not None:
        try:
            max_pairwise_voxels = int(max_pairwise_voxels)
        except (TypeError, ValueError):
            raise IBSIInputError("max_pairwise_voxels must be an integer of at least 2 or None") from None
        if max_pairwise_voxels < 2:
            raise IBSIInputError("max_pairwise_voxels must be at least 2 or None")

    morphology_mask = _as_bool_array(morphology_mask_xyz, name="morphology_mask_xyz") if morphology_mask_xyz is not None else base_mask.copy()
    intensity_mask = _as_bool_array(intensity_mask_xyz, name="intensity_mask_xyz") if intensity_mask_xyz is not None else base_mask.copy()
    if morphology_mask.shape != image.shape or intensity_mask.shape != image.shape:
        raise IBSIInputError("all masks must have the same shape as image_xyz")
    finite_mask = np.isfinite(image)
    invalid_count = int(np.count_nonzero(intensity_mask & ~finite_mask))
    intensity_mask = intensity_mask & finite_mask

    selected_slice: int | None = None
    if dimension_name == "2d":
        selected_slice = int(np.argmax(np.sum(intensity_mask, axis=(0, 1))))
        source_image = image[:, :, selected_slice]
        source_intensity_mask = intensity_mask[:, :, selected_slice]
        source_morphology_mask = morphology_mask[:, :, selected_slice]
        (
            working_image,
            working_intensity_mask,
            working_morphology_mask,
            crop_2d_slices,
            crop_2d_origin,
            crop_2d_context,
        ) = _minimal_crop(
            source_image,
            source_intensity_mask,
            source_morphology_mask,
            spacing[:2],
            include_local_context=False,
        )
        working_spacing: tuple[float, ...] = spacing[:2]
        crop_slices = (crop_2d_slices[0], crop_2d_slices[1], slice(selected_slice, selected_slice + 1))
        crop_origin_xyz = (crop_2d_origin[0], crop_2d_origin[1], selected_slice)
        crop_context_voxels = (crop_2d_context[0], crop_2d_context[1], 0)
    else:
        (
            working_image,
            working_intensity_mask,
            working_morphology_mask,
            crop_slices,
            crop_origin_xyz,
            crop_context_voxels,
        ) = _minimal_crop(
            image,
            intensity_mask,
            morphology_mask,
            spacing,
            include_local_context=True,
        )
        working_spacing = spacing

    spatial_diagnostics: dict[str, Any] = {}
    if spatial_method not in {"auto","blocked","fft"}:raise IBSIInputError("unsupported spatial method")
    features: dict[str, float] = {}
    unavailable: dict[str, str] = {}
    ivh_origin: float | None = None
    for spec in IBSI_FEATURE_CATALOG:
        if spec.standard_status == "not_standardized":
            unavailable[spec.key] = spec.source_note or "IBSI source marks this feature as not standardized"

    def put(code: str, value: Any) -> None:
        spec = IBSI_FEATURES_BY_CODE[code]
        key = spec.key
        if selected is not None and key not in selected:
            return
        try:
            converted = _float(value() if callable(value) else value)
        except Exception as error:
            mark_unavailable(code, "scalar formula failed: " + type(error).__name__ + ": " + str(error)[:250])
            return
        if spec.standard_status == "not_standardized":
            unavailable.setdefault(key, spec.source_note or "IBSI source marks this feature as not standardized")
            return
        if converted is None:
            unavailable.setdefault(key, "computed value was non-finite")
            return
        features[key] = converted
        unavailable.pop(key, None)

    def mark_unavailable(code: str, reason: str) -> None:
        key = IBSI_FEATURES_BY_CODE[code].key
        features.pop(key, None)
        unavailable[key] = str(reason)

    if dimension_name == "2d":
        morphology_reason = "IBSI morphology is defined in 3-D (DHQ4); dimension='2d' selects a max-ROI slice"
        for spec in IBSI_FEATURE_CATALOG:
            if spec.family in {"morphology", "local_intensity"}:
                mark_unavailable(spec.code, morphology_reason)
    else:
        if "morphology" in families:
            _morphology(
                working_image,
                working_intensity_mask,
                working_morphology_mask,
                working_spacing,  # type: ignore[arg-type]
                put=put,
                unavailable=mark_unavailable,
                max_pairwise_voxels=max_pairwise_voxels,
                spatial_method=spatial_method,
                spatial_diagnostics=spatial_diagnostics,
                calculate_diameter=selected is None or 'ibsi_L0JK' in selected,
                calculate_mesh=selected is None or bool(({s.key for s in IBSI_FEATURE_CATALOG if s.family=='morphology'} - {'ibsi_YEKZ','ibsi_KLMA','ibsi_N365','ibsi_NPT7'}) & selected),
                calculate_autocorrelation=selected is None or bool({'ibsi_N365','ibsi_NPT7'} & selected),
            )
        if "local_intensity" in families:
            # Local features use the cropped prepared volume, including every
            # centre-neighbourhood voxel needed by the ROI, exactly as specified
            # in IBSI section 3.2.
            local_values = working_image
            local_roi = working_intensity_mask
            if np.any(local_roi):
                roi_values = local_values[local_roi]
                max_value = float(np.max(roi_values))
                max_positions = np.argwhere(local_roi & (local_values == max_value))
                local_mean_map = _spherical_mean_map(local_values, spacing)
                sphere_means = local_mean_map[tuple(max_positions.T)]
                sphere_means = sphere_means[np.isfinite(sphere_means)]
                if sphere_means.size:
                    put("VJGA", lambda: (float(np.max(sphere_means))))
                else:
                    mark_unavailable("VJGA", "no finite voxels in any 1 cm^3 spherical neighbourhood")
                # Global peak may be evaluated for every ROI centre.  The same
                # full image volume is consulted for each neighbourhood.
                global_peak = local_mean_map[local_roi]
                global_peak = global_peak[np.isfinite(global_peak)]
                if global_peak.size:
                    put("0F91", lambda: (float(np.max(global_peak))))
                else:
                    mark_unavailable("0F91", "no finite voxels in any 1 cm^3 spherical neighbourhood")
            else:
                mark_unavailable("VJGA", "intensity mask contains no finite voxels")
                mark_unavailable("0F91", "intensity mask contains no finite voxels")

    continuous_values = working_image[working_intensity_mask]
    observed_intensity_min: float | None = None
    observed_intensity_max: float | None = None
    bin_origin_source = "declared" if declared_origin is not None else "roi_minimum"
    ivh_rounding: str | None = None
    group_evidence: dict[str, Any] = {}
    if "intensity_statistics" in families:
        _continuous_statistics(continuous_values, put=put, unavailable=mark_unavailable)
    if continuous_values.size:
        observed_intensity_min = float(np.min(continuous_values))
        observed_intensity_max = float(np.max(continuous_values))
        origin = observed_intensity_min if declared_origin is None else declared_origin
        # A declared fixed-bin origin is a lower bound.  Reject a ROI value
        # below it instead of clamping that value into grey level one; this
        # keeps texture and IVH definitions reproducible and makes an invalid
        # preprocessing range visible to the caller.
        if family_rules is None and declared_origin is not None and observed_intensity_min < declared_origin:
            raise IBSIInputError(
                "bin_origin exceeds the observed finite intensity minimum "
                f"({declared_origin} > {observed_intensity_min}); provide a lower bound or omit it"
            )
        if family_rules is None and families.intersection({"intensity_histogram", "glcm", "glrlm", "glszm", "gldzm", "ngtdm", "ngldm"}):
            quantization_image = np.where(np.isfinite(working_image), working_image, origin)
            q_working = np.floor((quantization_image - origin) / width).astype(np.int64) + 1
        q_histogram = q_texture = None
        if family_rules is not None:
            from app.services.feature_calculation.radiomics_discretisation import discretise
            for group, group_families in {
                "intensity_histogram": {"intensity_histogram"},
                "texture": {"glcm", "glrlm", "glszm", "gldzm", "ngtdm", "ngldm"},
            }.items():
                if not families.intersection(group_families):
                    continue
                try:
                    q, evidence = discretise(working_image, working_intensity_mask, family_rules[group])
                except ValueError as exc:
                    raise IBSIInputError(f"{group}: {exc}") from exc
                group_evidence[group] = evidence
                if q is None:
                    for spec in IBSI_FEATURE_CATALOG:
                        if spec.family in group_families:
                            mark_unavailable(spec.code, evidence["reason"])
                if group == "intensity_histogram":
                    q_histogram = q
                else:
                    q_texture = q
        else:
            if families.intersection({"intensity_histogram", "glcm", "glrlm", "glszm", "gldzm", "ngtdm", "ngldm"}):
                q_histogram = q_texture = q_working
        if "intensity_histogram" in families and q_histogram is not None:
            _q_values, _counts = _histogram_statistics(q_histogram[working_intensity_mask], put=put, unavailable=mark_unavailable)
        if "intensity_volume_histogram" in families:
            ivh_rule = family_rules["intensity_volume_histogram"] if family_rules else {"method": "auto"}
            if ivh_rule["method"] == "fbs":
                ivh_mode = "fixed_bin_size"
                ivh_width = ivh_rule["bin_width"]
                ivh_origin = observed_intensity_min if ivh_rule["bin_origin"] is None else ivh_rule["bin_origin"]
                if ivh_origin > observed_intensity_min:
                    raise IBSIInputError("intensity_volume_histogram: bin_origin exceeds the observed finite intensity minimum")
            else:
                ivh_origin = origin
            if ivh_mode == "discrete_calibrated":
                # CT values can become fractional after the default 1 mm B-spline
                # resampling.  The calibrated IVH definition still uses 1 HU
                # levels, so round only this IVH input to the nearest HU.  The
                # continuous statistics and texture quantisation above retain the
                # original fractional image values.
                ivh_rounding = "nearest_hu"
                ivh_values = np.rint(continuous_values).astype(np.int64)
                ivh_levels = np.arange(int(np.min(ivh_values)), int(np.max(ivh_values)) + 1, dtype=np.float64)
                ivh_q = ivh_values - int(np.min(ivh_values)) + 1
                _ivh(ivh_q, level_centres=ivh_levels, put=put, unavailable=mark_unavailable)
                ivh_origin = float(ivh_levels[0])
            else:
                ivh_q = np.floor((continuous_values - ivh_origin) / ivh_width).astype(np.int64) + 1
                _ivh(
                    ivh_q,
                    level_origin=ivh_origin,
                    level_width=ivh_width,
                    put=put,
                    unavailable=mark_unavailable,
                )
            if family_rules is not None:
                group_evidence["intensity_volume_histogram"] = {
                    "requested_method": ivh_rule["method"], "method": ivh_mode,
                    "bin_width": ivh_width, "bin_origin": ivh_origin,
                    "bin_origin_source": "declared" if ivh_rule.get("bin_origin") is not None else "roi_minimum",
                    "fallback_bin_width": width if ivh_rule["method"] == "auto" and ivh_mode == "fixed_bin_size" else None,
                    "rounding": ivh_rounding, "intensity_basis": "calculation_image",
                    "occupied_bin_count": int(np.unique(ivh_q).size),
                    "maximum_grey_level": int(np.max(ivh_q)),
                    "observed_intensity_range": [observed_intensity_min, observed_intensity_max],
                }
        if q_texture is not None and families.intersection({"glcm", "glrlm", "glszm", "gldzm", "ngtdm", "ngldm"}):
            if aggregation_name.startswith("merged"):
                _texture(
                    q_texture,
                    working_intensity_mask,
                    working_morphology_mask,
                    families=families,
                    distance=distance,
                    ngldm_alpha=alpha,
                    ngldm_delta=delta,
                    put=put,
                    unavailable=mark_unavailable,
                )
            else:
                reason = (
                    f"matrix aggregation '{aggregation_name}' is a standardized IBSI mode but this engine currently emits the merged mode only"
                )
                for spec in IBSI_FEATURE_CATALOG:
                    if spec.requires_discretisation and spec.family not in {"intensity_histogram", "intensity_volume_histogram"}:
                        mark_unavailable(spec.code, reason)
    else:
        origin = declared_origin
        for spec in IBSI_FEATURE_CATALOG:
            if spec.requires_discretisation:
                mark_unavailable(spec.code, "intensity mask contains no finite voxels")

    provenance = {
        "engine_version": IBSI_ENGINE_VERSION,
        "standard": IBSI_STANDARD,
        "source": IBSI_SOURCE,
        "modality": str(modality),
        "dimension": dimension_name,
        "selected_slice": selected_slice,
        "input_order": "xyz",
        "spacing_xyz": [float(value) for value in spacing],
        "binning": "per_family" if family_rules is not None else "fixed_bin_size",
        **({"discretisation": group_evidence, "discretisation_version": "ibsi-family-binning-v1"} if family_rules is not None else {}),
        "bin_width": width,
        "bin_origin": origin,
        "bin_origin_source": bin_origin_source,
        "observed_intensity_range": [observed_intensity_min, observed_intensity_max],
        "ivh_mode": ivh_mode,
        "ivh_bin_width": ivh_width,
        "ivh_bin_origin": ivh_origin,
        "ivh_rounding": ivh_rounding,
        "ivh_units": "image_intensity",
        "texture_aggregation": aggregation_name,
        "texture_distance": distance,
        "ngldm_alpha": alpha,
        "ngldm_delta": delta,
        "direct_pairwise_threshold": max_pairwise_voxels,
        "spatial_algorithms": spatial_diagnostics,
        "spatial_version": SPATIAL_VERSION,
        "finite_mask_excluded_voxels": invalid_count,
        "intensity_voxels": int(np.count_nonzero(intensity_mask)),
        "morphology_voxels": int(np.count_nonzero(morphology_mask)),
        "crop_shape": [int(value) for value in working_image.shape],
        "crop_input_shape": [int(value) for value in image.shape],
        "crop_origin_xyz": [int(value) for value in crop_origin_xyz],
        "crop_context_voxels": [int(value) for value in crop_context_voxels],
        "crop_context_mm": [float(crop_context_voxels[axis] * spacing[axis]) for axis in range(3)],
        "crop_slices_xyz": [[int(item.start or 0), int(item.stop or 0)] for item in crop_slices],
    }
    if selected is not None:
        features = {key: value for key, value in features.items() if key in selected}
        unavailable = {key: value for key, value in unavailable.items() if key in selected}
        provenance["selected_keys"] = sorted(selected)
    catalog_rows = _canonical_catalog_result(features, unavailable)
    if selected is not None:
        catalog_rows = [row for row in catalog_rows if row["key"] in selected]
    expected_count = IBSI_EXPECTED_FEATURE_COUNT if selected is None else len(selected)
    return {
        "features": dict(features),
        "unavailable": dict(unavailable),
        "not_standardized": {
            spec.key: spec.source_note or "IBSI source marks this feature as not standardized"
            for spec in IBSI_FEATURE_CATALOG
            if spec.standard_status == "not_standardized" and selected is None
        },
        "catalog": catalog_rows,
        "catalog_by_code": {row["code"]: row for row in catalog_rows},
        "feature_count": len(features),
        "expected_feature_count": expected_count,
        "counts": {
            "computed": len(features),
            "unavailable": sum(row["status"] == "unavailable" for row in catalog_rows),
            "not_standardized": sum(row["status"] == "not_standardized" for row in catalog_rows),
            "expected": expected_count,
        },
        "provenance": provenance,
        "engine_version": IBSI_ENGINE_VERSION,
    }


__all__ = ["IBSI_ENGINE_VERSION", "IBSIInputError", "compute_ibsi_features"]
