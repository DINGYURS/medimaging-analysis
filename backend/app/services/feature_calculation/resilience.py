"""Dependency-aware execution and local failures, without substitute values."""
from __future__ import annotations
from functools import cache
import numpy as np
from .catalog import get_research_feature_catalog


def dependency_plan(keys):
    catalog = {r['key']: r for r in get_research_feature_catalog()}
    selected = [catalog[k] for k in keys]
    return {
        'features': {r['key']: {'masks': r['required_masks'], 'discretisation': r['discretisation_group'], 'local_context': r['requires_local_context']} for r in selected},
        'morphology': any('morphological' in r['required_masks'] for r in selected),
        'intensity': any('intensity' in r['required_masks'] for r in selected),
        'local_context': any(r['requires_local_context'] for r in selected),
        'discretisation_groups': sorted({r['discretisation_group'] for r in selected} - {'none'}),
    }


def isolated_ibsi(compute, image, mask, spacing, keys, **kwargs):
    """Normal path stays one batch. On error retry independent families/keys.

    Shared failures can legitimately invalidate their dependent targets. No
    retry changes image, masks, spacing, bins, formulas, or selected outputs.
    """
    if not keys:
        return {'features': {}, 'unavailable': {}, 'provenance': {}}
    try:
        return compute(image, mask, spacing, selected_keys=keys, **kwargs)
    except Exception as initial:
        families = {}
        catalog = {r['key']: r for r in get_research_feature_catalog()}
        for key in keys:
            families.setdefault(catalog[key]['family'], []).append(key)
        values, unavailable, branches = {}, {}, []
        def attempt(targets):
            try:
                result = compute(image, mask, spacing, selected_keys=targets, **kwargs)
                values.update(result['features']); unavailable.update(result['unavailable'])
                branches.append({'keys': targets, 'provenance': result['provenance']})
            except Exception as error:
                if len(targets) > 1:
                    middle = len(targets) // 2
                    attempt(targets[:middle]); attempt(targets[middle:])
                else:
                    unavailable[targets[0]] = '特征依赖计算失败：' + type(error).__name__ + ': ' + str(error)[:300]
        for targets in families.values():
            attempt(targets)
        return {'features': values, 'unavailable': unavailable, 'provenance': {
            'failure_isolation': {'initial_error': type(initial).__name__ + ': ' + str(initial)[:300], 'branches': branches},
        }}


def isolated_custom(compute, image, mask, spacing, keys):
    """Keep the original 31-output fast path; recover independent dependencies.

    Recovery uses the same helpers and expressions as the original kernel.
    Cached shared dependencies prevent repeated work without hiding errors.
    """
    try:
        return compute(image, mask, spacing), {}
    except Exception:
        from . import lung_nodule_custom_features as kernel
        import math
        image_zyx = np.transpose(np.asarray(image, dtype=np.float32), (2, 1, 0))
        mask_zyx = np.transpose(np.asarray(mask) > 0, (2, 1, 0))
        syx = (float(spacing[1]), float(spacing[0]))
        voxel_volume = float(spacing[0] * spacing[1] * spacing[2])
        nvox = int(mask_zyx.sum())
        @cache
        def largest(): return kernel._largest_slice(mask_zyx)
        @cache
        def roi2d(): return mask_zyx[largest()]
        @cache
        def image2d(): return image_zyx[largest()]
        @cache
        def area(): return float(roi2d().sum() * syx[0] * syx[1])
        @cache
        def volume(): return float(nvox * voxel_volume)
        @cache
        def perimeter(): return kernel._perimeter_mm(roi2d(), syx)
        @cache
        def hull(): return kernel._convex_hull_area_perimeter(roi2d(), syx)
        @cache
        def threshold():
            value = kernel._otsu_threshold(image_zyx[mask_zyx])
            return -150.0 if value is None else value
        @cache
        def solid(): return int((mask_zyx & (image_zyx >= float(threshold()))).sum())
        @cache
        def gradients():
            chunks = []
            for z in range(mask_zyx.shape[0]):
                if mask_zyx[z].any():
                    values = kernel._boundary_gradients(image_zyx[z], mask_zyx[z], syx)
                    if values.size: chunks.append(values)
            return np.concatenate(chunks).astype(np.float64) if chunks else np.array([], dtype=np.float64)
        @cache
        def gradient_metrics():
            g = gradients()
            if not g.size: return None, None
            grad_range = float(g.max() - g.min()); q1 = float(np.percentile(g, 25)); q3 = float(np.percentile(g, 75))
            dmax = float(np.abs(g).max()) + 1e-8
            return float(np.sqrt(np.mean(g**2)) / dmax), (0.0 if grad_range < 1e-8 else float((q3 - q1) / (grad_range + 1e-8)))
        @cache
        def cdf(): return kernel._cdf_features(image_zyx, mask_zyx)
        def contour_circularity():
            boundary = np.column_stack(np.nonzero(kernel._boundary_mask(roi2d())))
            if boundary.shape[0] <= 2: return None
            centroid = np.column_stack(np.nonzero(roi2d())).mean(axis=0)
            distances = np.linalg.norm(boundary - centroid, axis=1)
            return float(distances.mean() / distances.std()) if float(distances.std()) > 1e-8 else None
        expressions = {
            'custom_ctr_threshold_hu': threshold,
            'custom_ctr_percent': lambda: solid() / nvox * 100.0,
            'custom_solid_volume_mm3': lambda: solid() * voxel_volume,
            'custom_solid_voxel_count': lambda: float(solid()),
            'custom_nodule_volume_mm3': volume,
            'custom_nodule_voxel_count': lambda: float(nvox),
            'custom_area_2d_mm2': area,
            'custom_perimeter_2d_mm': perimeter,
            'custom_equivalent_diameter_2d_mm': lambda: math.sqrt(4.0 * area() / math.pi) if area() > 0 else None,
            'custom_equivalent_diameter_3d_mm': lambda: (6.0 * volume() / math.pi) ** (1.0 / 3.0) if volume() > 0 else None,
            'custom_convex_area_2d_mm2': lambda: hull()[0],
            'custom_convex_perimeter_2d_mm': lambda: hull()[1],
            'custom_circularity_2d': lambda: 4.0 * math.pi * area() / hull()[1]**2 if hull()[1] and hull()[1] > 0 else None,
            'custom_radial_distance_std_2d_mm': lambda: kernel._radial_distance_std_mm(roi2d(), syx),
            'custom_margin_mean_boundary_gradient_hu_per_mm': lambda: gradients().mean() if gradients().size else None,
            'custom_margin_mean_abs_boundary_gradient_hu_per_mm': lambda: np.abs(gradients()).mean() if gradients().size else None,
            'custom_margin_std_boundary_gradient_hu_per_mm': lambda: gradients().std() if gradients().size else None,
            'custom_edge_sharpness_acutance_3d': lambda: gradient_metrics()[0],
            'custom_hsag': lambda: gradient_metrics()[1],
            'custom_boundary_convexity': lambda: kernel._convexity_by_slice(mask_zyx, syx),
            'custom_boundary_entropy_ratio': lambda: kernel._entropy_ratio(image_zyx, mask_zyx),
            'custom_contour_convexity': lambda: hull()[1] / max(perimeter(), 1e-8) if hull()[1] else None,
            'custom_contour_solidity': lambda: area() / hull()[0] if hull()[0] else None,
            'custom_contour_circularity': contour_circularity,
            'custom_contour_compactness': lambda: 4.0 * math.pi * area() / perimeter()**2 if perimeter() > 0 else None,
        }
        values, errors = {}, {}
        for key in keys:
            try:
                value = cdf().get(key) if key.startswith('custom_cdf_') else expressions[key]()
                if value is not None and np.isfinite(value): values[key] = float(value)
                else: errors[key] = '原公式在当前ROI未产生有限值'
            except Exception as error:
                errors[key] = '特征依赖计算失败：' + type(error).__name__ + ': ' + str(error)[:300]
        return values, errors
