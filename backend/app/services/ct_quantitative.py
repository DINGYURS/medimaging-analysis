"""Deterministic measurements on the verified, native CT/ROI grid."""
from itertools import combinations

import numpy as np
import SimpleITK as sitk
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull, QhullError, cKDTree
from skimage.measure import marching_cubes, mesh_surface_area

from app.services.ct_reference_evidence import LOBE_LABELS, MIN_LOBE_OVERLAP, axial_measurement
from app.services.feature_calculation.spatial import diameter

PERCENTILES = (5, 10, 25, 50, 75, 90, 95)
HU_BOUNDS = (-1000, -850, -600, -300, -150, 0, 100, 200, 400, 1000)
SHELL_MM = 1.0
REGION_SEARCH_MM = 50.0
PLANES = ("sagittal", "coronal", "axial")
SIDES = {"left": (10, 11), "right": (12, 13, 14)}
PROTOCOL = {
    "grid": "native", "resampling": "none", "normalization": "none", "resegmentation": "none",
    "mesh": "marching_cubes_level_0.5_zero_padded",
    "planar_diameter": "same_plane_occupied_voxel_centers",
    "planar_orientation_tolerance_degrees": 0.1,
    "percentiles": list(PERCENTILES), "percentile_interpolation": "linear",
    "histogram_bin_edges_hu": list(HU_BOUNDS), "histogram_bins": "left_closed_right_open_with_underflow_and_overflow",
    "intensity_shell_mm": SHELL_MM, "shell_distance": "to_nearest_non_roi_voxel_center",
    "gradient": "central_differences_in_mm_inside_boundary_voxels",
    "lobe_assignment_minimum_fraction": MIN_LOBE_OVERLAP,
    "region_search_radius_mm": REGION_SEARCH_MM,
}


def plane_axes(image):
    direction = np.asarray(image.GetDirection()).reshape(3, 3)
    axes = np.argmax(np.abs(direction), axis=1)
    aligned = np.abs(direction[np.arange(3), axes]) >= np.cos(np.deg2rad(0.1))
    return {name: 2 - int(axis) for name, axis, valid in zip(PLANES, axes, aligned) if valid}


def mesh(roi, spacing):
    vertices, faces, _, _ = marching_cubes(np.pad(roi, 1), level=0.5, spacing=spacing,
                                          allow_degenerate=False)
    triangles = vertices[faces].astype(np.float64)
    volume = float(abs(np.einsum("ij,ij->i", triangles[:, 0],
                                 np.cross(triangles[:, 1], triangles[:, 2])).sum()) / 6)
    return vertices, float(mesh_surface_area(vertices, faces)), volume


def planar(roi, bounds, spacing, axis):
    order = [axis, *(dim for dim in range(3) if dim != axis)]
    return axial_measurement(roi.transpose(order), tuple(bounds[dim] for dim in order),
                             tuple(spacing[dim] for dim in order[:0:-1]))


def hu_summary(values):
    values = np.asarray(values, dtype=np.float64)
    if not values.size or not np.isfinite(values).all():
        return None
    q = np.percentile(values, PERCENTILES, method="linear")
    bins = np.bincount(np.searchsorted(HU_BOUNDS, values, side="right"), minlength=len(HU_BOUNDS) + 1)
    shares = bins / values.size
    nonzero = shares[shares > 0]
    return {
        "mean_hu": float(values.mean()), "median_hu": float(q[3]),
        "min_hu": float(values.min()), "max_hu": float(values.max()),
        "p05_hu": float(q[0]), "p10_hu": float(q[1]), "p25_hu": float(q[2]),
        "p75_hu": float(q[4]), "p90_hu": float(q[5]), "p95_hu": float(q[6]),
        "iqr_hu": float(q[4] - q[2]), "std_hu": float(values.std()),
        "fixed_bin_entropy_bit": float(-np.sum(nonzero * np.log2(nonzero))),
        "histogram_counts": bins.tolist(), "bin_fractions": shares.tolist(),
    }


def contour(roi, spacing, axis):
    axes = [dim for dim in range(3) if dim != axis]
    areas, deficits = [], []
    pixel_area = float(np.prod([spacing[dim] for dim in axes]))
    corners = np.array([[-.5, -.5], [-.5, .5], [.5, -.5], [.5, .5]])
    for plane in np.moveaxis(roi, axis, 0):
        if not plane.any():
            areas.append(0.0)
            continue
        area = float(plane.sum() * pixel_area)
        areas.append(area)
        boundary = np.argwhere(plane & ~ndi.binary_erosion(plane))
        xy = (boundary[:, None, :] + corners).reshape(-1, 2)
        hull_area = float(ConvexHull(xy).volume * pixel_area)
        deficits.append(float(np.clip(1 - area / hull_area, 0, 1)))
    return {
        "areas_mm2": areas, "occupied_slices": int(np.count_nonzero(areas)),
        "max_area_mm2": max(areas), "mean_convex_hull_deficit": float(np.mean(deficits)),
        "max_convex_hull_deficit": max(deficits),
        "largest_adjacent_area_change_mm2": float(np.max(np.abs(np.diff(areas)))) if len(areas) > 1 else None,
    }


def expanded_bounds(bounds, shape, padding):
    return tuple(slice(max(0, b.start - int(p)), min(n, b.stop + int(p)))
                 for b, n, p in zip(bounds, shape, padding))


def edge_features(volume, bounds, roi, spacing):
    border = tuple(b.start > 0 and b.stop < n for b, n in zip(bounds, volume.shape))
    distance = ndi.distance_transform_edt(np.pad(roi, 1), sampling=spacing)[1:-1, 1:-1, 1:-1]
    core = roi & (distance > SHELL_MM)
    shell = roi & ~core
    out = {"shell_voxels": int(shell.sum()), "core_voxels": int(core.sum())}
    if not all(border):
        return out, "ROI 接触扫描边缘，缺少完整邻域"
    image = volume[bounds]
    if core.any() and shell.any():
        out["core_minus_shell_hu"] = float(image[core].mean(dtype=np.float64)
                                          - image[shell].mean(dtype=np.float64))
    crop = expanded_bounds(bounds, volume.shape, (1, 1, 1))
    context = np.asarray(volume[crop], dtype=np.float64)
    exact = tuple(slice(b.start - c.start, b.stop - c.start) for b, c in zip(bounds, crop))
    boundary = roi & ~ndi.binary_erosion(roi)
    gradients = [derivative[exact][boundary] for derivative in np.gradient(context, *spacing)]
    magnitude = np.sqrt(np.sum(np.square(gradients), axis=0))
    if magnitude.size and np.isfinite(magnitude).all():
        out["boundary_gradient_mean_hu_per_mm"] = float(magnitude.mean())
        out["boundary_gradient_p90_hu_per_mm"] = float(np.percentile(magnitude, 90))
    return out, None if core.any() and shell.any() else "当前掩膜与原始层距下无法同时取得核心和边缘体素"


def solid_features(volume, bounds, roi, spacing, axial, threshold, full_axial):
    solid = roi & (volume[bounds] >= threshold)
    count = int(solid.sum())
    output = {"threshold_hu": threshold, "voxels": count, "volume_mm3": float(count * np.prod(spacing)),
              "volume_fraction_percent": 100 * count / int(roi.sum())}
    if not count:
        return output
    components, n = ndi.label(solid)
    regions = ndi.find_objects(components)
    output["connected_components"] = n
    # Never draw a diameter across disconnected components of threshold-positive tissue.
    max_3d, max_axial = [], []
    for label, region in enumerate(regions, 1):
        component = components[region] == label
        component_bounds = tuple(slice(b.start + r.start, b.start + r.stop) for b, r in zip(bounds, region))
        vertices, _, _ = mesh(component, spacing)
        max_3d.append(diameter(vertices)[0])
        value = planar(component, component_bounds, spacing, axial) if axial is not None else None
        if value:
            max_axial.append(value["raw_mm"])
    output["max_component_diameter_3d_mm"] = max(max_3d)
    if max_axial and full_axial:
        output["max_component_axial_diameter_mm"] = max(max_axial)
        output["axial_diameter_ratio_percent"] = 100 * max(max_axial) / full_axial["raw_mm"]
    return output


def lesion_geometry(roi, bounds, spacing, axes, volume_shape):
    vertices, area, volume = mesh(roi, spacing)
    points = np.argwhere(roi) * spacing
    eigenvalues = np.maximum(np.linalg.eigvalsh(np.cov(points.T, bias=True)), 0) if len(points) > 1 else None
    max_diameter, method = diameter(vertices)
    geometry = {
        "voxels": int(roi.sum()), "voxel_volume_mm3": float(roi.sum() * np.prod(spacing)),
        "mesh_volume_mm3": volume, "surface_mm2": area, "diameter_3d_mm": max_diameter,
        "diameter_algorithm": method, "sphericity": float(np.cbrt(np.pi * (6 * volume) ** 2) / area),
        "touches_volume_boundary": any(b.start == 0 or b.stop == n for b, n in zip(bounds, volume_shape)),
        "diameters": {name: planar(roi, bounds, spacing, axis) for name, axis in axes.items()},
    }
    if eigenvalues is not None and eigenvalues[-1] > 0:
        geometry["elongation"] = float(np.sqrt(eigenvalues[1] / eigenvalues[2]))
        geometry["flatness"] = float(np.sqrt(eigenvalues[0] / eigenvalues[2]))
    try:
        hull_volume = float(ConvexHull(vertices).volume)
        geometry["convex_hull_volume_deficit"] = float(np.clip(1 - volume / hull_volume, 0, 1))
    except QhullError:
        pass
    if "axial" in axes:
        geometry["axial_contours"] = contour(roi, spacing, axes["axial"])
    return geometry


def region_distance(labels, bounds, roi, spacing, kind):
    box = expanded_bounds(bounds, labels.shape, np.ceil(REGION_SEARCH_MM / spacing) + 1)
    target = labels[box] > 0
    relative = tuple(slice(b.start - c.start, b.stop - c.start) for b, c in zip(bounds, box))
    if kind == "outer_lung":
        # Fill internal cavities and remove fissures by using the union of all lobe labels.
        target = ndi.binary_fill_holes(target)
        target &= ~ndi.binary_erosion(target, border_value=1)
    overlap = int(np.count_nonzero(target[relative] & roi))
    if overlap:
        return {"distance_mm": 0.0, "overlap_voxels": overlap}
    if not target.any():
        return {"distance_mm": None, "reason": "no_mask_voxel_within_search_radius"}
    points = (np.argwhere(roi) + [r.start for r in relative]) * spacing
    targets = np.argwhere(target) * spacing
    distance = float(cKDTree(targets).query(points, k=1, distance_upper_bound=REGION_SEARCH_MM)[0].min())
    if not np.isfinite(distance):
        return {"distance_mm": None, "reason": "no_mask_voxel_within_search_radius"}
    return {"distance_mm": distance, "overlap_voxels": 0}


def lobe_fractions(labels, bounds, roi):
    values = labels[bounds][roi]
    fractions = {label: float(np.count_nonzero(values == label) / values.size) for label in LOBE_LABELS}
    best = max(fractions, key=fractions.get)
    side = next((name for name, ids in SIDES.items() if sum(fractions[key] for key in ids) >= MIN_LOBE_OVERLAP), None)
    return {"fractions": {LOBE_LABELS[k]: v for k, v in fractions.items()},
            "unassigned_fraction": float(np.count_nonzero(values == 0) / values.size),
            "lobe": LOBE_LABELS[best] if fractions[best] >= MIN_LOBE_OVERLAP else None,
            "side": side}


def organ_metrics(masks, volume, voxel_volume, hu_verified):
    organs = {}
    if "lobes" in masks:
        labels = masks["lobes"]
        lobe_volumes = {key: float(np.count_nonzero(labels == key) * voxel_volume / 1000) for key in LOBE_LABELS}
        lungs = {"volume_ml": sum(lobe_volumes.values()),
                 "lobe_volumes_ml": {LOBE_LABELS[k]: v for k, v in lobe_volumes.items()}}
        for side, ids in SIDES.items():
            lungs[f"{side}_volume_ml"] = sum(lobe_volumes[k] for k in ids)
        lungs["left_fraction_percent"] = 100 * lungs["left_volume_ml"] / lungs["volume_ml"]
        lungs["right_fraction_percent"] = 100 * lungs["right_volume_ml"] / lungs["volume_ml"]
        if hu_verified:
            lungs["whole_lung_mask_hu"] = hu_summary(volume[labels > 0])
        organs["lungs"] = lungs
    # Liver metrics require a separate liver workflow with a known liver-lesion ROI.
    return organs


def distribution(lesions, centers):
    lobe_counts, side_counts = {}, {}
    for lesion in lesions:
        location = lesion.get("location") or {}
        for field, counts in (("lobe", lobe_counts), ("side", side_counts)):
            key = location.get(field) or "unknown"
            counts[key] = counts.get(key, 0) + 1
    distances = [{"lesions": [a, b], "center_distance_mm": float(np.linalg.norm(np.subtract(x, y)))}
                 for (a, x), (b, y) in combinations(centers.items(), 2)]
    return {"candidate_count": len(lesions), "counts_by_lobe": lobe_counts,
            "counts_by_side": side_counts, "center_distances": distances}
