"""Pure-computation research features for a supplied liver/tumour ROI.

The public function in this module deliberately has no database, DICOM, model,
or modality-specific threshold dependency.  ``image_xyz`` and ``mask_xyz``
share an ``(x, y, z)`` array convention and ``spacing_xyz`` is in millimetres
for those same axes.  The supplied mask is treated as the ROI; it is not
silently interpreted as a whole-liver mask.

All shape calculations use physical coordinates derived from the supplied
spacing.  In particular, an MR image is allowed to contain arbitrary finite
signal units, and no CT/HU threshold is used anywhere in this module.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull, QhullError
from skimage.measure import find_contours, marching_cubes


VERSION = "liver_radiomics_features_v1.0.1"
_HISTOGRAM_BINS = 32
_MAX_CURVE_POINTS = 256
_MAX_DIAMETER_POINTS = 4096
_EPSILON = 1.0e-12
_STRUCTURE_3D = np.ones((3, 3, 3), dtype=bool)


def _definition(
    formula: str,
    unit: str,
    value_range: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "formula": formula,
        "unit": unit,
        "range": value_range,
        "research_only": True,
        "notes": notes,
    }


# The definitions intentionally name the physical quantity instead of relying
# on overloaded historical names such as convexity, CDF, or compactness.
_DEFINITIONS: dict[str, dict[str, Any]] = {
    "liver_voxel_volume_mm3": _definition(
        "N(mask > 0) * sx * sy * sz",
        "mm^3",
        "[0, infinity)",
        "Occupancy volume of the supplied ROI using native voxel spacing.",
    ),
    "liver_equivalent_diameter_3d_mm": _definition(
        "(6 * V_voxel / pi)^(1/3)",
        "mm",
        "[0, infinity)",
        "Diameter of a sphere with the same voxel-occupancy volume.",
    ),
    "liver_mesh_surface_area_mm2": _definition(
        "sum over padded marching-cubes triangles of ||cross(e1, e2)|| / 2",
        "mm^2",
        "(0, infinity)",
        "Closed isosurface area from a one-voxel-zero-padded binary ROI; physical spacing is applied per axis.",
    ),
    "liver_mesh_volume_mm3": _definition(
        "abs(sum over oriented mesh triangles dot(v0, cross(v1, v2)) / 6)",
        "mm^3",
        "(0, infinity)",
        "Closed padded marching-cubes volume, independent of the arbitrary mesh origin.",
    ),
    "liver_mesh_sphericity": _definition(
        "pi^(1/3) * (6 * V_mesh)^(2/3) / A_mesh",
        "dimensionless",
        "(0, 1] for an ideal valid closed mesh",
        "One for a sphere in the continuous idealization; discretization can create small deviations.",
    ),
    "liver_mesh_compactness1": _definition(
        "V_mesh / (sqrt(pi) * A_mesh^(3/2))",
        "dimensionless",
        "(0, 1/(6*pi)] for an ideal valid closed mesh",
        "Explicit physical formula for compactness 1; this is not a synonym for a convexity ratio.",
    ),
    "liver_mesh_compactness2": _definition(
        "36 * pi * V_mesh^2 / A_mesh^3",
        "dimensionless",
        "(0, 1] for an ideal valid closed mesh",
        "Explicit physical formula for compactness 2; it is numerically the cube of the stated sphericity formula.",
    ),
    "liver_axial_max_contour_area_mm2": _definition(
        "shoelace area of the largest outer contour on the axial (z-normal) slice with greatest physical contour area",
        "mm^2",
        "[0, infinity)",
        "Contour coordinates come from find_contours at mask level 0.5 and are scaled by sx, sy.",
    ),
    "liver_axial_max_contour_perimeter_mm": _definition(
        "sum of physical Euclidean lengths of successive closed outer-contour segments",
        "mm",
        "(0, infinity)",
        "Measured on the same axial contour used for the max-area slice.",
    ),
    "liver_axial_max_equivalent_diameter_2d_mm": _definition(
        "2 * sqrt(A_axial / pi)",
        "mm",
        "[0, infinity)",
        "Diameter of a circle with the same physical area as the selected axial contour.",
    ),
    "liver_axial_max_convex_area_mm2": _definition(
        "area of the 2-D convex hull of the selected axial contour points",
        "mm^2",
        "[A_axial, infinity)",
        "Convex envelope is computed from the same physical contour points, not from voxel-count area.",
    ),
    "liver_axial_max_convex_perimeter_mm": _definition(
        "perimeter of the 2-D convex hull of the selected axial contour points",
        "mm",
        "(0, infinity)",
        "Convex-hull perimeter corresponding to the selected physical contour.",
    ),
    "liver_axial_max_solidity": _definition(
        "A_axial / A_convex_hull_axial",
        "dimensionless",
        "(0, 1] for a valid simple outer contour",
        "Area solidity; unlike a historical convexity label, this uses contour area and its point-hull area with one common definition.",
    ),
    "liver_axial_max_planar_convexity": _definition(
        "P_convex_hull_axial / P_axial",
        "dimensionless",
        "(0, 1] for a valid simple outer contour",
        "Perimeter convexity of the same selected axial contour; this is deliberately named planar convexity rather than an ambiguous convexity 1/2.",
    ),
    "liver_axial_max_circularity": _definition(
        "4 * pi * A_axial / P_axial^2",
        "dimensionless",
        "(0, 1] for an ideal simple planar curve",
        "Planar circularity of the selected physical contour; it is not a 3-D sphericity measure.",
    ),
    "liver_convex_hull_volume_to_mesh_volume_ratio": _definition(
        "V_convex_hull_3d / V_mesh",
        "dimensionless",
        "[1, infinity) for a valid closed ROI mesh",
        "Named hull-to-mesh ratio to avoid reversing the similarly named historical convexity variants.",
    ),
    "liver_convex_hull_area_to_mesh_surface_area_ratio": _definition(
        "A_convex_hull_3d / A_mesh",
        "dimensionless",
        "(0, 1] for a valid closed ROI mesh under the convex-hull surface-area inequality",
        "3-D surface-area hull-to-mesh ratio, distinct from axial solidity and from a volume ratio.",
    ),
    "liver_3d_solidity": _definition(
        "V_mesh / V_convex_hull_3d",
        "dimensionless",
        "(0, 1] for a valid closed ROI mesh",
        "Standard 3-D volume solidity; it is the mesh-to-hull direction, not the reciprocal hull-to-mesh volume ratio.",
    ),
    "liver_pca_major_axis_length_mm": _definition(
        "4 * sqrt(lambda_major), where lambda are eigenvalues of physical voxel-center covariance",
        "mm",
        "[0, infinity)",
        "Major PCA axis from supplied ROI voxel centers in native physical coordinates.",
    ),
    "liver_pca_minor_axis_length_mm": _definition(
        "4 * sqrt(lambda_minor), where lambda are eigenvalues of physical voxel-center covariance",
        "mm",
        "[0, infinity)",
        "Minor PCA axis from the same physical voxel-center covariance.",
    ),
    "liver_pca_least_axis_length_mm": _definition(
        "4 * sqrt(lambda_least), where lambda are eigenvalues of physical voxel-center covariance",
        "mm",
        "[0, infinity)",
        "Least PCA axis from the same physical voxel-center covariance.",
    ),
    "liver_pca_mean_axis_length_mm": _definition(
        "mean(4 * sqrt(lambda_major), 4 * sqrt(lambda_minor), 4 * sqrt(lambda_least))",
        "mm",
        "[0, infinity)",
        "Arithmetic mean of the three explicitly defined 4*sqrt(lambda) physical PCA axes.",
    ),
    "liver_max_2d_diameter_axial_xy_mm": _definition(
        "max over z slices of max pairwise physical distance among that slice's contour convex-hull points in x-y",
        "mm",
        "[0, infinity)",
        "Only points from one common axial slice are compared; pairwise distances are bounded and chunked.",
    ),
    "liver_max_2d_diameter_coronal_xz_mm": _definition(
        "max over y slices of max pairwise physical distance among that slice's contour convex-hull points in x-z",
        "mm",
        "[0, infinity)",
        "Only points from one common coronal slice are compared; pairwise distances are bounded and chunked.",
    ),
    "liver_max_2d_diameter_sagittal_yz_mm": _definition(
        "max over x slices of max pairwise physical distance among that slice's contour convex-hull points in y-z",
        "mm",
        "[0, infinity)",
        "Only points from one common sagittal slice are compared; pairwise distances are bounded and chunked.",
    ),
    "liver_radial_distance_std_mm": _definition(
        "std(r_boundary), r measured from the physical ROI voxel-center centroid",
        "mm",
        "[0, infinity)",
        "Unnormalized physical radial standard deviation of the one-voxel 3-D ROI boundary.",
    ),
    "liver_radial_distance_std_normalized_by_max": _definition(
        "std(r_boundary / max(r_boundary)), r measured from the physical ROI voxel-center centroid",
        "dimensionless",
        "[0, 1] when max radius > 0",
        "Literature-style normalized radial standard deviation uses r/r_max; no clinical threshold is implied.",
    ),
    "liver_radial_distance_coefficient_of_variation": _definition(
        "std(r_boundary) / mean(r_boundary)",
        "dimensionless",
        "[0, infinity) when mean radius > 0",
        "Coefficient of variation retained as a separate quantity so it is not confused with r/r_max normalization.",
    ),
    "liver_boundary_gradient_magnitude_mean_signal_per_mm": _definition(
        "mean(||physical_gradient(image)||) over one-voxel 3-D ROI boundary",
        "image-signal-units/mm",
        "[0, infinity)",
        "Gradient is computed from native image signal with supplied physical spacing; sign is removed by magnitude.",
    ),
    "liver_boundary_entropy_bits": _definition(
        "-sum(p_i * log2(p_i)) for boundary intensities using shared ROI-derived histogram edges",
        "bits",
        "[0, log2(32)]",
        "Boundary entropy uses the same fixed number and edges of bins as core entropy.",
    ),
    "liver_core_entropy_bits": _definition(
        "-sum(p_i * log2(p_i)) for a two-voxel-eroded core using the boundary/core shared histogram edges",
        "bits",
        "[0, log2(32)]",
        "Only available when the two-voxel-eroded core is nonempty.",
    ),
    "liver_boundary_to_core_entropy_ratio": _definition(
        "H_boundary / H_core",
        "dimensionless",
        "[0, infinity) when H_core > 0",
        "Uses identical ROI-derived bin edges; unavailable when the core entropy denominator is zero.",
    ),
    "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean": _definition(
        "(mean(I_inner_2px) - mean(I_outer_2px)) / mean(I_supplied_ROI)",
        "dimensionless",
        "real-valued when the supplied ROI mean is nonzero",
        "Signed two-pixel inner/outer rings are measured on the selected maximum-area axial slice; the external in-image ring can include non-liver tissue and is not equivalent to a 1 cm intrahepatic peritumoral ROI. Only the supplied ROI mean is the normalization denominator.",
    ),
    "liver_energy": _definition(
        "use original_firstorder_Energy from the full radiomics output",
        "(image-signal-units)^2",
        "provider-defined",
        "This module does not create a competing custom Energy feature; use the original_firstorder_Energy value directly.",
    ),
    "liver_uniformity": _definition(
        "use original_firstorder_Uniformity from the full radiomics output",
        "dimensionless",
        "provider-defined",
        "This module does not create a competing custom Uniformity feature; use the original_firstorder_Uniformity value directly.",
    ),
    "liver_radial_cdf": _definition(
        "F(r) = n({boundary radii <= r}) / n(boundary radii), with r divided by max boundary radius",
        "x dimensionless normalized radius; y probability",
        "x in [0, 1], y in (0, 1]",
        "Empirical radial CDF curve sampled at at most 256 sorted observations; it is separate from intensity CDF.",
    ),
    "liver_boundary_intensity_cdf": _definition(
        "F(t) = n({boundary signal <= t}) / n(boundary samples)",
        "x image-signal-units; y probability",
        "x in observed finite boundary signal range, y in (0, 1]",
        "Empirical boundary-intensity CDF in native arbitrary image units, with no HU conversion or threshold.",
    ),
}

_STATIC_UNAVAILABLE: dict[str, str] = {
    "liver_convexity1": "unavailable: the source document reverses convexity labels; explicit hull-to-mesh volume and area ratios are reported instead.",
    "liver_convexity2": "unavailable: the source document reverses convexity labels; explicit hull-to-mesh volume and area ratios are reported instead.",
    "liver_tumor_liver_volume_fraction": "unavailable: requires a separate whole-liver mask for the denominator; the supplied ROI mask is insufficient.",
    "liver_ctr": "unavailable: requires entity/solid and/or necrotic masks; no automatic CT/HU thresholding is performed.",
    "liver_porosity": "unavailable: requires validated entity/solid/void or necrotic masks; a binary ROI alone cannot establish porosity.",
    "liver_fractal_dimension": "unavailable: no validated scale range and estimator were supplied for this study.",
    "liver_lacunarity": "unavailable: no validated spatial sampling scale, window scheme, and estimator were supplied.",
    "liver_vdt": "unavailable: requires paired follow-up examinations and dates.",
    "liver_adc_quantitative": "unavailable: requires ADC data with quantitative scaling units.",
    "liver_ivim": "unavailable: requires a multi-b-value acquisition and its signal model inputs.",
    "liver_tlr": "unavailable: requires a separately defined reference ROI.",
    "liver_tar": "unavailable: requires a separately defined reference ROI.",
    "liver_star": "unavailable: requires externally supplied training statistics/calibration.",
    "liver_dce": "unavailable: requires a registered time axis and dynamic contrast series.",
    "liver_peritumoral_1cm": "unavailable: requires liver and vascular exclusion masks for a reliable 1 cm intrahepatic ring.",
    "liver_lbi": "unavailable: the cited paper definition/algorithm is not specified sufficiently for a reproducible computation.",
    "liver_spiculation": "unavailable: the cited paper definition/algorithm is not specified sufficiently for a reproducible computation.",
    "liver_lobulation": "unavailable: the cited paper definition/algorithm is not specified sufficiently for a reproducible computation.",
    "liver_ithscore": "unavailable: the cited paper definition/algorithm is not specified sufficiently for a reproducible computation.",
}

for _key, _reason in _STATIC_UNAVAILABLE.items():
    _DEFINITIONS.setdefault(
        _key,
        _definition(
            "not computed from the supplied image/ROI inputs",
            "not applicable",
            "not applicable",
            _reason.removeprefix("unavailable: ").strip(),
        ),
    )


def _validate_inputs(
    image_xyz: Any,
    mask_xyz: Any,
    spacing_xyz: Any,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float]]:
    try:
        # Preserve the caller's numeric image dtype during validation.  Only
        # the ROI crop is promoted by downstream numerical routines, avoiding
        # a second full-volume float64 MRI allocation.
        image = np.asarray(image_xyz)
        mask_values = np.asarray(mask_xyz)
    except (TypeError, ValueError) as exc:
        raise ValueError("image_xyz and mask_xyz must be numeric 3-D arrays") from exc

    if image.ndim != 3 or mask_values.ndim != 3:
        raise ValueError("image_xyz and mask_xyz must both be 3-D arrays")
    if image.shape != mask_values.shape:
        raise ValueError("image_xyz and mask_xyz must share a 3D shape")
    valid_numeric = (np.issubdtype(image.dtype, np.number) and not np.issubdtype(image.dtype, np.complexfloating)) or np.issubdtype(image.dtype, np.bool_)
    valid_mask_numeric = (np.issubdtype(mask_values.dtype, np.number) and not np.issubdtype(mask_values.dtype, np.complexfloating)) or np.issubdtype(mask_values.dtype, np.bool_)
    if not valid_numeric or not valid_mask_numeric:
        raise ValueError("image_xyz and mask_xyz must be real numeric arrays")
    if not np.all(np.isfinite(image)):
        raise ValueError("image_xyz must contain only finite values")
    if not np.all(np.isfinite(mask_values)):
        raise ValueError("mask_xyz must contain only finite values")

    try:
        spacing = np.asarray(spacing_xyz, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("spacing_xyz must contain three finite positive values") from exc
    if spacing.shape != (3,) or not np.all(np.isfinite(spacing)) or np.any(spacing <= 0):
        raise ValueError("spacing_xyz must contain three finite positive values")

    return image, mask_values > 0, (float(spacing[0]), float(spacing[1]), float(spacing[2]))


def _roi_bbox_slices(mask: np.ndarray, halo: int = 3) -> tuple[slice, slice, slice]:
    """Return a bounded ROI crop with enough context for 2-pixel rings/gradients."""

    # Projection-wise bounds avoid materialising an ``N x 3`` coordinate array
    # for a large ROI, which would defeat the memory-saving purpose of the
    # downstream crop.
    occupied = tuple(
        np.any(
            mask,
            axis=tuple(axis for axis in range(mask.ndim) if axis != dimension),
        )
        for dimension in range(mask.ndim)
    )
    if any(not projection.any() for projection in occupied):
        raise ValueError("an empty ROI has no bounding box")
    halo = max(int(halo), 0)
    lower = np.asarray(
        [np.flatnonzero(projection)[0] for projection in occupied],
        dtype=np.int64,
    )
    upper = np.asarray(
        [np.flatnonzero(projection)[-1] + 1 for projection in occupied],
        dtype=np.int64,
    )
    lower = np.maximum(lower - halo, 0)
    upper = np.minimum(upper + halo, np.asarray(mask.shape))
    return tuple(slice(int(start), int(stop)) for start, stop in zip(lower, upper))  # type: ignore[return-value]


def _put_feature(features: dict[str, float], key: str, value: Any) -> None:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return
    if np.isfinite(numeric):
        features[key] = numeric


def _safe_mean(values: Any) -> float | None:
    """Return a finite mean without overflowing an intermediate sum."""

    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if array.size == 0 or not np.all(np.isfinite(array)):
        return None
    scale = float(np.max(np.abs(array)))
    if scale == 0.0:
        return 0.0
    with np.errstate(over="ignore", invalid="ignore"):
        mean = float(np.mean(array / scale) * scale)
    return mean if np.isfinite(mean) else None


def _put_unavailable(unavailable: dict[str, str], key: str, reason: str) -> None:
    unavailable[key] = str(reason)


def _mesh_geometry(
    mask: np.ndarray,
    spacing: tuple[float, float, float],
) -> tuple[np.ndarray, np.ndarray, float, float] | None:
    """Return vertices, faces, physical surface area and physical volume."""

    padded = np.pad(mask.astype(np.float32), 1, mode="constant", constant_values=0)
    try:
        vertices, faces, _normals, _values = marching_cubes(
            padded,
            level=0.5,
            spacing=spacing,
            allow_degenerate=False,
        )
    except (ValueError, RuntimeError):
        return None
    if vertices.ndim != 2 or vertices.shape[1] != 3 or faces.size == 0:
        return None

    triangles = vertices[faces]
    edge_1 = triangles[:, 1] - triangles[:, 0]
    edge_2 = triangles[:, 2] - triangles[:, 0]
    cross = np.cross(edge_1, edge_2)
    surface_area = 0.5 * float(np.linalg.norm(cross, axis=1).sum())

    # Centering makes the signed tetrahedron sum numerically stable despite the
    # one-voxel padding translation.
    centered = triangles - vertices.mean(axis=0)
    signed_volume = np.einsum(
        "ij,ij->i",
        centered[:, 0],
        np.cross(centered[:, 1], centered[:, 2]),
    ).sum() / 6.0
    volume = abs(float(signed_volume))
    if not np.isfinite(surface_area) or not np.isfinite(volume) or surface_area <= 0 or volume <= 0:
        return None
    return vertices.astype(np.float64), faces.astype(np.int64), surface_area, volume


def _polygon_area_perimeter(points: np.ndarray) -> tuple[float, float]:
    if points.shape[0] < 3:
        return 0.0, 0.0
    closed = np.vstack((points, points[0]))
    perimeter = float(np.linalg.norm(np.diff(closed, axis=0), axis=1).sum())
    area = 0.5 * abs(
        float(
            np.dot(points[:, 0], np.roll(points[:, 1], -1))
            - np.dot(points[:, 1], np.roll(points[:, 0], -1))
        )
    )
    return area, perimeter


def _largest_axial_contour(
    mask: np.ndarray,
    spacing: tuple[float, float, float],
) -> tuple[np.ndarray, float, float, int] | None:
    best: tuple[np.ndarray, float, float, int] | None = None
    for z in range(mask.shape[2]):
        for points, area, perimeter in _plane_contour_candidates(mask[:, :, z], spacing[:2]):
            if best is None or area > best[1]:
                best = (points, area, perimeter, z)
    return best


def _plane_contour_candidates(
    mask_2d: np.ndarray,
    plane_spacing: tuple[float, float],
) -> list[tuple[np.ndarray, float, float]]:
    """Return closed physical contours from one padded binary 2-D plane."""

    if not mask_2d.any():
        return []
    padded = np.pad(mask_2d.astype(np.float64), 1, mode="constant", constant_values=0)
    try:
        contours = find_contours(padded, level=0.5)
    except (ValueError, RuntimeError):
        return []
    result: list[tuple[np.ndarray, float, float]] = []
    for contour in contours:
        if contour.shape[0] < 3 or not np.all(np.isfinite(contour)):
            continue
        points = np.column_stack((contour[:, 0] - 1.0, contour[:, 1] - 1.0))
        points = points * np.asarray(plane_spacing, dtype=np.float64)
        area, perimeter = _polygon_area_perimeter(points)
        if area > 0 and perimeter > 0:
            result.append((points, area, perimeter))
    return result


def _convex_hull_2d(points: np.ndarray) -> tuple[float, float] | None:
    if points.shape[0] < 3:
        return None
    try:
        hull = ConvexHull(points)
    except (QhullError, ValueError, RuntimeError):
        return None
    area = float(hull.volume)
    perimeter = float(hull.area)
    if not np.isfinite(area) or not np.isfinite(perimeter) or area <= 0 or perimeter <= 0:
        return None
    return area, perimeter


def _convex_hull_3d(points: np.ndarray) -> tuple[float, float] | None:
    if points.shape[0] < 4:
        return None
    try:
        hull = ConvexHull(points)
    except (QhullError, ValueError, RuntimeError):
        return None
    volume = float(hull.volume)
    area = float(hull.area)
    if not np.isfinite(volume) or not np.isfinite(area) or volume <= 0 or area <= 0:
        return None
    return volume, area


def _pca_axis_lengths(mask: np.ndarray, spacing: tuple[float, float, float]) -> np.ndarray | None:
    indices = np.argwhere(mask)
    if indices.shape[0] == 0:
        return None
    coordinates = (indices.astype(np.float64) + 0.5) * np.asarray(spacing, dtype=np.float64)
    centered = coordinates - coordinates.mean(axis=0)
    covariance = (centered.T @ centered) / float(coordinates.shape[0])
    try:
        eigenvalues = np.linalg.eigvalsh(covariance)
    except np.linalg.LinAlgError:
        return None
    eigenvalues = np.maximum(np.asarray(eigenvalues, dtype=np.float64), 0.0)
    # eigvalsh is ascending; expose major, minor, least in descending order.
    return 4.0 * np.sqrt(eigenvalues[::-1])


def _bounded_points(points: np.ndarray, maximum: int) -> np.ndarray:
    if points.shape[0] <= maximum:
        return points
    indices = np.linspace(0, points.shape[0] - 1, maximum, dtype=np.int64)
    return points[np.unique(indices)]


def _max_pairwise_distance(points: np.ndarray) -> float | None:
    points = _bounded_points(np.asarray(points, dtype=np.float64), _MAX_DIAMETER_POINTS)
    if points.shape[0] < 2:
        return None
    maximum_squared = 0.0
    # Chunk one side so the distance matrix remains bounded for detailed
    # marching-cubes surfaces.
    chunk_size = 512
    for start in range(0, points.shape[0], chunk_size):
        block = points[start : start + chunk_size]
        delta = block[:, None, :] - points[None, :, :]
        squared = np.einsum("ijk,ijk->ij", delta, delta)
        block_max = float(squared.max())
        if block_max > maximum_squared:
            maximum_squared = block_max
    result = math.sqrt(maximum_squared)
    return result if np.isfinite(result) else None


def _max_same_plane_diameter(
    mask: np.ndarray,
    spacing: tuple[float, float, float],
    plane_axes: tuple[int, int],
    normal_axis: int,
) -> float | None:
    """Find the largest diameter among contours from one common plane.

    The previous mesh-projection shortcut compared vertices from different
    slices.  Here every candidate is built from one 2-D mask plane, so a
    separated pair of components on different slices cannot inflate a
    diameter in that orientation.
    """

    plane_spacing = (spacing[plane_axes[0]], spacing[plane_axes[1]])
    best: float | None = None
    for plane_index in range(mask.shape[normal_axis]):
        plane = np.take(mask, plane_index, axis=normal_axis)
        candidates = _plane_contour_candidates(plane, plane_spacing)
        if not candidates:
            continue
        # All points below are from this one physical plane.  Combining
        # disconnected components in a single slice is intentional, while
        # retaining the same-slice restriction.
        points = np.vstack([candidate[0] for candidate in candidates])
        points = np.unique(points, axis=0)
        try:
            hull = ConvexHull(points)
            points = points[hull.vertices]
        except (QhullError, ValueError, RuntimeError):
            points = _bounded_points(points, _MAX_DIAMETER_POINTS)
        diameter = _max_pairwise_distance(points)
        if diameter is not None and (best is None or diameter > best):
            best = diameter
    return best


def _boundary_mask(mask: np.ndarray) -> np.ndarray:
    eroded = ndi.binary_erosion(mask, structure=_STRUCTURE_3D, iterations=1, border_value=0)
    return mask & ~eroded


def _physical_gradient_magnitude(image: np.ndarray, spacing: tuple[float, float, float]) -> np.ndarray:
    # A boolean image is a valid finite numeric input for this pure function,
    # but NumPy deliberately does not define subtraction for bool arrays.
    # Promote only the already bounded ROI crop, keeping the full-volume
    # validation/allocation behavior unchanged.
    image = np.asarray(image, dtype=np.float64)
    squared = np.zeros(image.shape, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        for axis, step in enumerate(spacing):
            if image.shape[axis] < 2:
                continue
            gradient = np.gradient(image, step, axis=axis, edge_order=1)
            squared += np.asarray(gradient, dtype=np.float64) ** 2
        return np.sqrt(squared)


def _shared_histogram_edges(values: np.ndarray, bins: int) -> np.ndarray:
    minimum = float(values.min())
    maximum = float(values.max())
    if math.isclose(minimum, maximum, rel_tol=0.0, abs_tol=0.0):
        half_width = max(abs(minimum) * 1.0e-6, 1.0e-6)
        lower = np.longdouble(minimum) - np.longdouble(half_width)
        upper = np.longdouble(maximum) + np.longdouble(half_width)
    else:
        lower = np.longdouble(minimum)
        upper = np.longdouble(maximum)

    # Interpolating in long double avoids the float64 ``maximum - minimum``
    # overflow for a finite range spanning both signs.  It also preserves
    # very close but distinct finite levels long enough for np.histogram to
    # receive strictly increasing bins.
    fractions = np.linspace(
        np.longdouble(0.0),
        np.longdouble(1.0),
        bins + 1,
        dtype=np.longdouble,
    )
    return lower * (np.longdouble(1.0) - fractions) + upper * fractions


def _entropy(values: np.ndarray, edges: np.ndarray) -> float:
    # NumPy warns when histogramming bool arrays; the feature is defined on
    # numeric signal values, so promote this bounded sample before binning.
    counts, _ = np.histogram(np.asarray(values, dtype=np.float64), bins=edges)
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    probabilities = counts[counts > 0].astype(np.float64) / total
    if probabilities.size <= 1:
        return 0.0
    return max(0.0, float(-(probabilities * np.log2(probabilities)).sum()))


def _empirical_cdf(values: np.ndarray) -> dict[str, list[float]] | None:
    finite_values = np.asarray(values, dtype=np.float64)
    finite_values = finite_values[np.isfinite(finite_values)]
    if finite_values.size == 0:
        return None
    sorted_values = np.sort(finite_values)
    x_unique, counts = np.unique(sorted_values, return_counts=True)
    y_unique = np.cumsum(counts, dtype=np.float64) / float(sorted_values.size)
    if x_unique.size > _MAX_CURVE_POINTS:
        indices = np.unique(
            np.linspace(0, x_unique.size - 1, _MAX_CURVE_POINTS, dtype=np.int64)
        )
    else:
        indices = np.arange(x_unique.size, dtype=np.int64)
    x_values = x_unique[indices]
    y_values = y_unique[indices]
    return {
        "x": [float(value) for value in x_values],
        "y": [float(value) for value in y_values],
    }


def _mark_geometry_unavailable(unavailable: dict[str, str], reason: str) -> None:
    for key in (
        "liver_mesh_surface_area_mm2",
        "liver_mesh_volume_mm3",
        "liver_mesh_sphericity",
        "liver_mesh_compactness1",
        "liver_mesh_compactness2",
        "liver_convex_hull_volume_to_mesh_volume_ratio",
        "liver_convex_hull_area_to_mesh_surface_area_ratio",
        "liver_3d_solidity",
        "liver_max_2d_diameter_axial_xy_mm",
        "liver_max_2d_diameter_coronal_xz_mm",
        "liver_max_2d_diameter_sagittal_yz_mm",
    ):
        _put_unavailable(unavailable, key, reason)


def compute_liver_radiomics_features(
    image_xyz: Any,
    mask_xyz: Any,
    spacing_xyz: tuple[float, float, float],
    *,
    modality: str = "MR",
) -> dict[str, Any]:
    """Compute finite, JSON-safe liver ROI research features.

    ``modality`` is accepted for API compatibility and descriptive context,
    but it never triggers an intensity threshold.  Empty finite ROIs return a
    structured result with zero occupancy volume and unavailable morphology.
    Invalid shapes, non-finite inputs, or non-positive spacing raise
    ``ValueError`` before any feature calculation.
    """

    del modality  # The calculations are deliberately modality agnostic.
    image, mask, spacing = _validate_inputs(image_xyz, mask_xyz, spacing_xyz)
    features: dict[str, float] = {}
    unavailable: dict[str, str] = dict(_STATIC_UNAVAILABLE)
    curves: dict[str, dict[str, list[float]]] = {}
    definitions = copy.deepcopy(_DEFINITIONS)

    voxel_volume = float(np.prod(np.asarray(spacing, dtype=np.float64)))
    voxel_count = int(mask.sum())
    occupancy_volume = float(voxel_count * voxel_volume)
    _put_feature(features, "liver_voxel_volume_mm3", occupancy_volume)
    _put_feature(
        features,
        "liver_equivalent_diameter_3d_mm",
        (6.0 * occupancy_volume / math.pi) ** (1.0 / 3.0) if occupancy_volume > 0 else 0.0,
    )

    if voxel_count == 0:
        empty_reason = "unavailable: supplied ROI mask is empty"
        for key in (
            "liver_mesh_surface_area_mm2",
            "liver_mesh_volume_mm3",
            "liver_mesh_sphericity",
            "liver_mesh_compactness1",
            "liver_mesh_compactness2",
            "liver_axial_max_contour_area_mm2",
            "liver_axial_max_contour_perimeter_mm",
            "liver_axial_max_equivalent_diameter_2d_mm",
            "liver_axial_max_convex_area_mm2",
            "liver_axial_max_convex_perimeter_mm",
            "liver_axial_max_solidity",
            "liver_axial_max_planar_convexity",
            "liver_axial_max_circularity",
            "liver_convex_hull_volume_to_mesh_volume_ratio",
            "liver_convex_hull_area_to_mesh_surface_area_ratio",
            "liver_3d_solidity",
            "liver_pca_major_axis_length_mm",
            "liver_pca_minor_axis_length_mm",
            "liver_pca_least_axis_length_mm",
            "liver_pca_mean_axis_length_mm",
            "liver_max_2d_diameter_axial_xy_mm",
            "liver_max_2d_diameter_coronal_xz_mm",
            "liver_max_2d_diameter_sagittal_yz_mm",
            "liver_radial_distance_std_mm",
            "liver_radial_distance_std_normalized_by_max",
            "liver_radial_distance_coefficient_of_variation",
            "liver_boundary_gradient_magnitude_mean_signal_per_mm",
            "liver_boundary_entropy_bits",
            "liver_core_entropy_bits",
            "liver_boundary_to_core_entropy_ratio",
            "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
        ):
            _put_unavailable(unavailable, key, empty_reason)
        _put_unavailable(unavailable, "liver_radial_cdf", empty_reason)
        _put_unavailable(unavailable, "liver_boundary_intensity_cdf", empty_reason)
        return {
            "features": features,
            "definitions": definitions,
            "unavailable": unavailable,
            "curves": curves,
            "version": VERSION,
        }

    roi_slices = _roi_bbox_slices(mask, halo=3)
    working_image = image[roi_slices]
    working_mask = mask[roi_slices]

    mesh = _mesh_geometry(working_mask, spacing)
    if mesh is None:
        _mark_geometry_unavailable(
            unavailable,
            "unavailable: padded marching-cubes could not produce a valid closed mesh",
        )
    else:
        vertices, faces, surface_area, mesh_volume = mesh
        _put_feature(features, "liver_mesh_surface_area_mm2", surface_area)
        _put_feature(features, "liver_mesh_volume_mm3", mesh_volume)
        if surface_area > 0 and mesh_volume > 0:
            _put_feature(
                features,
                "liver_mesh_sphericity",
                math.pi ** (1.0 / 3.0) * (6.0 * mesh_volume) ** (2.0 / 3.0) / surface_area,
            )
            _put_feature(
                features,
                "liver_mesh_compactness1",
                mesh_volume / (math.sqrt(math.pi) * surface_area ** 1.5),
            )
            _put_feature(
                features,
                "liver_mesh_compactness2",
                36.0 * math.pi * mesh_volume**2 / surface_area**3,
            )

        hull_3d = _convex_hull_3d(vertices)
        if hull_3d is None:
            _put_unavailable(
                unavailable,
                "liver_convex_hull_volume_to_mesh_volume_ratio",
                "unavailable: 3-D convex hull is degenerate for this ROI mesh",
            )
            _put_unavailable(
                unavailable,
                "liver_convex_hull_area_to_mesh_surface_area_ratio",
                "unavailable: 3-D convex hull is degenerate for this ROI mesh",
            )
            _put_unavailable(
                unavailable,
                "liver_3d_solidity",
                "unavailable: 3-D convex hull is degenerate for this ROI mesh",
            )
        else:
            hull_volume, hull_area = hull_3d
            _put_feature(
                features,
                "liver_convex_hull_volume_to_mesh_volume_ratio",
                hull_volume / mesh_volume,
            )
            _put_feature(
                features,
                "liver_convex_hull_area_to_mesh_surface_area_ratio",
                hull_area / surface_area,
            )
            _put_feature(features, "liver_3d_solidity", min(1.0, max(0.0, mesh_volume / hull_volume)))
        for key, axes in (
            ("liver_max_2d_diameter_axial_xy_mm", (0, 1, 2)),
            ("liver_max_2d_diameter_coronal_xz_mm", (0, 2, 1)),
            ("liver_max_2d_diameter_sagittal_yz_mm", (1, 2, 0)),
        ):
            diameter = _max_same_plane_diameter(working_mask, spacing, (axes[0], axes[1]), axes[2])
            if diameter is None:
                _put_unavailable(unavailable, key, "unavailable: no valid same-plane 2-D contour diameter")
            else:
                _put_feature(features, key, diameter)

    contour = _largest_axial_contour(working_mask, spacing)
    if contour is None:
        for key in (
            "liver_axial_max_contour_area_mm2",
            "liver_axial_max_contour_perimeter_mm",
            "liver_axial_max_equivalent_diameter_2d_mm",
            "liver_axial_max_convex_area_mm2",
            "liver_axial_max_convex_perimeter_mm",
            "liver_axial_max_solidity",
            "liver_axial_max_planar_convexity",
            "liver_axial_max_circularity",
        ):
            _put_unavailable(unavailable, key, "unavailable: no closed axial contour was found")
    else:
        contour_points, contour_area, contour_perimeter, _selected_axial_z = contour
        _put_feature(features, "liver_axial_max_contour_area_mm2", contour_area)
        _put_feature(features, "liver_axial_max_contour_perimeter_mm", contour_perimeter)
        _put_feature(
            features,
            "liver_axial_max_equivalent_diameter_2d_mm",
            2.0 * math.sqrt(contour_area / math.pi),
        )
        hull_2d = _convex_hull_2d(contour_points)
        if hull_2d is None:
            for key in (
                "liver_axial_max_convex_area_mm2",
                "liver_axial_max_convex_perimeter_mm",
                "liver_axial_max_solidity",
                "liver_axial_max_planar_convexity",
            ):
                _put_unavailable(unavailable, key, "unavailable: selected axial contour has a degenerate convex hull")
        else:
            convex_area, convex_perimeter = hull_2d
            _put_feature(features, "liver_axial_max_convex_area_mm2", convex_area)
            _put_feature(features, "liver_axial_max_convex_perimeter_mm", convex_perimeter)
            _put_feature(features, "liver_axial_max_solidity", min(1.0, max(0.0, contour_area / convex_area)))
            _put_feature(
                features,
                "liver_axial_max_planar_convexity",
                min(1.0, max(0.0, convex_perimeter / contour_perimeter)),
            )
        if contour_perimeter > 0:
            _put_feature(
                features,
                "liver_axial_max_circularity",
                4.0 * math.pi * contour_area / contour_perimeter**2,
            )

    pca_axes = _pca_axis_lengths(working_mask, spacing)
    if pca_axes is None:
        for key in (
            "liver_pca_major_axis_length_mm",
            "liver_pca_minor_axis_length_mm",
            "liver_pca_least_axis_length_mm",
            "liver_pca_mean_axis_length_mm",
        ):
            _put_unavailable(unavailable, key, "unavailable: physical ROI point covariance is undefined")
    else:
        for key, value in zip(
            (
                "liver_pca_major_axis_length_mm",
                "liver_pca_minor_axis_length_mm",
                "liver_pca_least_axis_length_mm",
            ),
            pca_axes,
        ):
            _put_feature(features, key, value)
        _put_feature(features, "liver_pca_mean_axis_length_mm", float(pca_axes.mean()))

    boundary = _boundary_mask(working_mask)
    boundary_indices = np.argwhere(boundary)
    roi_indices = np.argwhere(working_mask)
    roi_coordinates = (roi_indices.astype(np.float64) + 0.5) * np.asarray(spacing, dtype=np.float64)
    boundary_coordinates = (boundary_indices.astype(np.float64) + 0.5) * np.asarray(spacing, dtype=np.float64)
    centroid = roi_coordinates.mean(axis=0)
    radial_distances = np.linalg.norm(boundary_coordinates - centroid, axis=1)
    radial_mean = float(radial_distances.mean()) if radial_distances.size else 0.0
    radial_std = float(radial_distances.std())
    _put_feature(features, "liver_radial_distance_std_mm", radial_std)
    radial_max = float(radial_distances.max()) if radial_distances.size else 0.0
    if radial_max > _EPSILON:
        _put_feature(
            features,
            "liver_radial_distance_std_normalized_by_max",
            float((radial_distances / radial_max).std()),
        )
    else:
        _put_unavailable(
            unavailable,
            "liver_radial_distance_std_normalized_by_max",
            "unavailable: physical boundary maximum radius is zero",
        )
    if radial_mean > _EPSILON:
        _put_feature(
            features,
            "liver_radial_distance_coefficient_of_variation",
            radial_std / radial_mean,
        )
    else:
        _put_unavailable(
            unavailable,
            "liver_radial_distance_coefficient_of_variation",
            "unavailable: physical boundary mean radius is zero",
        )

    if radial_max > _EPSILON:
        normalized_radial = radial_distances / radial_max
        radial_curve = _empirical_cdf(normalized_radial)
    else:
        normalized_radial = None
        radial_curve = None
    intensity_curve = _empirical_cdf(working_image[boundary])
    if radial_curve is None:
        radial_reason = (
            "unavailable: physical boundary maximum radius is zero"
            if normalized_radial is None
            else "unavailable: no finite boundary radii"
        )
        _put_unavailable(unavailable, "liver_radial_cdf", radial_reason)
    else:
        curves["liver_radial_cdf"] = radial_curve
    if intensity_curve is None:
        _put_unavailable(unavailable, "liver_boundary_intensity_cdf", "unavailable: no finite boundary signal values")
    else:
        curves["liver_boundary_intensity_cdf"] = intensity_curve

    gradient_magnitude = _physical_gradient_magnitude(working_image, spacing)
    if boundary.any():
        gradient_mean = _safe_mean(gradient_magnitude[boundary])
        if gradient_mean is None:
            _put_unavailable(
                unavailable,
                "liver_boundary_gradient_magnitude_mean_signal_per_mm",
                "unavailable: physical gradient contains non-finite values",
            )
        else:
            _put_feature(
                features,
                "liver_boundary_gradient_magnitude_mean_signal_per_mm",
                gradient_mean,
            )
    else:
        _put_unavailable(
            unavailable,
            "liver_boundary_gradient_magnitude_mean_signal_per_mm",
            "unavailable: no one-voxel 3-D ROI boundary",
        )

    roi_values = working_image[working_mask]
    histogram_edges = _shared_histogram_edges(roi_values, _HISTOGRAM_BINS)
    if boundary.any():
        _put_feature(
            features,
            "liver_boundary_entropy_bits",
            _entropy(working_image[boundary], histogram_edges),
        )
    else:
        _put_unavailable(unavailable, "liver_boundary_entropy_bits", "unavailable: no ROI boundary samples")

    core = ndi.binary_erosion(working_mask, structure=_STRUCTURE_3D, iterations=2, border_value=0)
    if core.any():
        _put_feature(features, "liver_core_entropy_bits", _entropy(working_image[core], histogram_edges))
        core_entropy = _entropy(working_image[core], histogram_edges)
        boundary_entropy = _entropy(working_image[boundary], histogram_edges) if boundary.any() else None
        if boundary_entropy is not None and core_entropy > _EPSILON:
            _put_feature(
                features,
                "liver_boundary_to_core_entropy_ratio",
                boundary_entropy / core_entropy,
            )
        else:
            _put_unavailable(
                unavailable,
                "liver_boundary_to_core_entropy_ratio",
                "unavailable: core entropy denominator is zero",
            )
    else:
        _put_unavailable(
            unavailable,
            "liver_core_entropy_bits",
            "unavailable: two-voxel-eroded ROI core is empty",
        )
        _put_unavailable(
            unavailable,
            "liver_boundary_to_core_entropy_ratio",
            "unavailable: two-voxel-eroded ROI core is empty",
        )

    tumor_mean = _safe_mean(roi_values)
    if contour is None:
        inner_ring = np.zeros((0, 0), dtype=bool)
        outer_ring = np.zeros((0, 0), dtype=bool)
        image_mgc = np.zeros((0, 0), dtype=np.float64)
    else:
        selected_axial_z = contour[3]
        image_mgc = working_image[:, :, selected_axial_z]
        mask_mgc = working_mask[:, :, selected_axial_z]
        structure_2d = np.ones((3, 3), dtype=bool)
        inner_ring = mask_mgc & ~ndi.binary_erosion(mask_mgc, structure=structure_2d, iterations=2, border_value=0)
        outer_ring = ndi.binary_dilation(mask_mgc, structure=structure_2d, iterations=2) & ~mask_mgc
    if not inner_ring.any() or not outer_ring.any():
        _put_unavailable(
            unavailable,
            "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
            "unavailable: two-voxel inner or outer ring is empty",
        )
    elif tumor_mean is None:
        _put_unavailable(
            unavailable,
            "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
            "unavailable: supplied ROI mean is non-finite",
        )
    elif abs(tumor_mean) <= _EPSILON:
        _put_unavailable(
            unavailable,
            "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
            "unavailable: supplied ROI mean is zero, so MGC normalization is unsafe",
        )
    else:
        inner_mean = _safe_mean(image_mgc[inner_ring])
        outer_mean = _safe_mean(image_mgc[outer_ring])
        if inner_mean is None or outer_mean is None:
            _put_unavailable(
                unavailable,
                "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
                "unavailable: inner or outer ring mean is non-finite",
            )
        else:
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                mgc = (inner_mean - outer_mean) / tumor_mean
            if np.isfinite(mgc):
                _put_feature(
                    features,
                    "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
                    mgc,
                )
            else:
                _put_unavailable(
                    unavailable,
                    "liver_mgc_2pixel_inner_minus_outer_over_tumor_mean",
                    "unavailable: MGC contrast overflowed finite numeric range",
                )

    # Keep the result contract JSON-safe even if a future numerical backend
    # changes a scalar type.  All values inserted above are finite by design.
    return {
        "features": {str(key): float(value) for key, value in features.items()},
        "definitions": definitions,
        "unavailable": {str(key): str(value) for key, value in unavailable.items()},
        "curves": curves,
        "version": VERSION,
    }


__all__ = ["compute_liver_radiomics_features", "VERSION"]
