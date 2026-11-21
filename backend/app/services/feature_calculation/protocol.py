"""Shared image/mask preparation for platform radiomics.

The application has several radiomics entry points (the immediate workspace
extractor, automatic refreshes, research jobs, and the Excel export).  This
module is deliberately independent of any one of those entry points.  It
defines the analysis grid and prepares an image and mask on that grid together
so that a feature implementation cannot accidentally use a resampled image
with a native mask (or vice versa).

Annotation coordinates and slice indices remain in the source grid.  The
returned arrays are an analysis representation only; callers should retain
the source geometry in their result provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import SimpleITK as sitk




PROTOCOL_VERSION = "shared-radiomics-grid-v2"
DEFAULT_TARGET_SPACING_MM = (1.0, 1.0, 1.0)
MAX_ANALYSIS_VOXELS = 256_000_000
NATIVE_TARGET_SPACING = None
_UNSET = object()


class RadiomicsProtocolError(ValueError):
    """A requested protocol or source grid cannot be represented safely."""

    resegmentation_stats: dict[str, Any] | None = None


def _as_finite_positive(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RadiomicsProtocolError(f"{name} must contain finite positive numbers") from exc
    if not math.isfinite(number) or number <= 0:
        raise RadiomicsProtocolError(f"{name} must contain finite positive numbers")
    return number


def _spacing(value: Any, *, name: str = "spacing") -> tuple[float, float, float]:
    if isinstance(value, str) or not isinstance(value, Sequence) or len(value) != 3:
        raise RadiomicsProtocolError(f"{name} must be a 3-vector of finite positive numbers")
    return tuple(_as_finite_positive(item, name=name) for item in value)  # type: ignore[return-value]


def _canonical_target_spacing(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().casefold() in {"none", "native", "original", "source"}:
            return None
        raise RadiomicsProtocolError("target_spacing must be 'native'/'none' or a 3-vector")
    return _spacing(value, name="target_spacing")


def _recommended_bin_width(modality: str | None) -> float:
    return 5.0 if str(modality or "").strip().upper() in {"MR", "MRI"} else 25.0


@dataclass(frozen=True)
class RadiomicsProtocol:
    """Resolved, hashable description of one radiomics analysis protocol.

    ``target_spacing_mm=None`` is the explicit source/native grid mode.
    ``target_spacing_mm=(1, 1, 1)`` is the platform's shared isotropic
    default.  Callers that need source geometry must request ``"none"`` or
    pass an explicit native protocol object.
    """

    target_spacing_mm: tuple[float, float, float] | None = DEFAULT_TARGET_SPACING_MM
    normalization: str = "none"
    normalization_scale: float = 1.0
    normalization_scope: str = "none"
    bin_width: float = 25.0
    image_interpolator: str = "sitkBSpline"
    mask_interpolator: str = "sitkNearestNeighbor"
    mask_threshold: float = 0.5
    resegmentation: Mapping[str, Any] | None = None
    processing_order: str = "legacy"
    discretisation: Mapping[str, Any] | None = None
    source_version: str = "legacy"
    version: str = PROTOCOL_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_native(self) -> bool:
        return self.target_spacing_mm is None

    @property
    def target_spacing(self) -> tuple[float, float, float] | None:
        return self.target_spacing_mm

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.version,
            "target_mode": "native" if self.is_native else "resampled",
            "target_spacing_mm": None
            if self.target_spacing_mm is None
            else [float(item) for item in self.target_spacing_mm],
            "target_spacing": None
            if self.target_spacing_mm is None
            else [float(item) for item in self.target_spacing_mm],
            "normalization": self.normalization,
            "normalization_scale": float(self.normalization_scale),
            "normalization_scope": self.normalization_scope,
            "bin_width": float(self.bin_width),
            "interpolators": {
                "image": self.image_interpolator,
                "mask": self.mask_interpolator,
            },
        }
        # Keep legacy nearest-neighbour identities unchanged. Only active
        # linear interpolation introduces a new numerical protocol setting.
        if not self.is_native and self.mask_interpolator == "sitkLinear":
            payload["mask_threshold"] = float(self.mask_threshold)
        if self.extra:
            payload["extra"] = _jsonable(dict(self.extra))
        if self.resegmentation:
            payload["resegmentation"] = dict(self.resegmentation)
            payload["processing_order"] = "interpolate-resegment-normalize-discretize-calculate-v1"
        if self.processing_order == "interpolate_then_normalize":
            payload["processing_order"] = self.processing_order
        if self.discretisation is not None:
            payload["discretisation"] = dict(self.discretisation)
            payload["discretisation_version"] = "ibsi-family-binning-v1"
        if self.source_version != "legacy":
            payload["source_version"] = self.source_version
        return payload

    def hash(self) -> str:
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_radiomics_protocol(
    config: Mapping[str, Any] | RadiomicsProtocol | None = None,
    *,
    modality: str | None = None,
    target_spacing: Any = _UNSET,
    normalization: str | None | object = _UNSET,
    normalize: bool | None | object = _UNSET,
    normalization_scale: float | None | object = _UNSET,
    normalization_scope: str | None | object = _UNSET,
    bin_width: float | None | object = _UNSET,
    default_target_spacing: Any = _UNSET,
) -> RadiomicsProtocol:
    """Resolve legacy config fields and the shared grid protocol.

    ``resampling`` remains a supported legacy spelling.  ``target_spacing``
    and ``analysis_spacing`` are accepted for new callers.  A missing or
    ``None`` target selects the shared 1 mm default; native mode is explicit
    via ``"none"``/``"native"`` or a native :class:`RadiomicsProtocol`.
    """

    if isinstance(config, RadiomicsProtocol):
        base = config
        if all(value is _UNSET for value in (target_spacing, normalization, normalize, normalization_scale, bin_width)):
            return base
        config = base.as_dict()
    values = dict(config or {})
    source_version = values.get("source_version", "legacy")
    if source_version not in {"legacy", "physical-v1"}:
        raise RadiomicsProtocolError("unsupported source_version")
    order = values.get("processing_order", "legacy")
    # v4 exported this descriptive order; preserve its round-trip identity.
    if order == "interpolate-resegment-normalize-discretize-calculate-v1":
        order = "legacy"
    if order not in {"legacy", "interpolate_then_normalize"}:
        raise RadiomicsProtocolError("unsupported processing_order")
    from app.services.feature_calculation.parameters import NoResegmentation, RangeResegmentation
    resegmentation = values.get("resegmentation") or {"method": "none"}
    if resegmentation.get("method") == "none":
        NoResegmentation.model_validate(resegmentation)
        resegmentation = None
    else:
        resegmentation = RangeResegmentation.model_validate(resegmentation).model_dump()

    if target_spacing is _UNSET:
        for key in ("target_spacing", "target_spacing_mm", "analysis_spacing", "resampling"):
            if key in values:
                target_spacing = values[key]
                break
        else:
            target_spacing = (
                default_target_spacing
                if default_target_spacing is not _UNSET and default_target_spacing is not None
                else DEFAULT_TARGET_SPACING_MM
            )
    if target_spacing is None:
        target_spacing = DEFAULT_TARGET_SPACING_MM
    target = _canonical_target_spacing(target_spacing)

    if normalization is _UNSET:
        normalization = values.get("normalization", "auto")
    if normalize is not _UNSET and normalize is not None:
        normalization = "z-score" if bool(normalize) else "none"
    normalized_name = str(normalization or "none").strip().casefold()
    if normalized_name == "auto":
        normalized_name = (
            "z-score" if str(modality or "").strip().upper() in {"MR", "MRI"} else "none"
        )
    if normalized_name not in {"none", "z-score"}:
        raise RadiomicsProtocolError("normalization must be 'none', 'z-score', or 'auto'")

    if normalization_scale is _UNSET:
        normalization_scale = values.get("normalization_scale", 100.0 if normalized_name == "z-score" else 1.0)
    scale = _as_finite_positive(normalization_scale, name="normalization_scale")
    if normalized_name == "none":
        scale = 1.0
    if normalization_scope is _UNSET:
        scope = values.get("normalization_scope")
    else:
        scope = normalization_scope
    if normalized_name == "z-score":
        # Legacy calls retain the original order. Research callers can opt
        # into post-interpolation normalization independently of range filtering.
        scope = "full_analysis_grid" if resegmentation or order == "interpolate_then_normalize" else "full_native_image"
    else:
        scope = "none"

    if bin_width is _UNSET:
        bin_width = values.get("bin_width", _recommended_bin_width(modality))
    width = _as_finite_positive(bin_width, name="bin_width")
    discretisation = values.get("discretisation")
    if discretisation is not None:
        from app.services.feature_calculation.parameters import PlanDiscretisation
        discretisation = PlanDiscretisation.model_validate(discretisation).model_dump()

    interpolators = values.get("interpolators") or {}
    image_interpolator = str(values.get("image_interpolator") or interpolators.get("image") or "sitkBSpline")
    mask_interpolator = str(values.get("mask_interpolator") or interpolators.get("mask") or "sitkNearestNeighbor")
    if image_interpolator not in {"sitkBSpline", "sitkLinear", "sitkNearestNeighbor"}:
        raise RadiomicsProtocolError(f"unsupported image interpolator: {image_interpolator}")
    if mask_interpolator not in {"sitkNearestNeighbor", "sitkLinear"}:
        raise RadiomicsProtocolError("mask_interpolator must be sitkNearestNeighbor or sitkLinear")
    threshold_value = values.get("mask_threshold", 0.5)
    if isinstance(threshold_value, bool):
        raise RadiomicsProtocolError("mask_threshold must be finite and in (0, 1]")
    mask_threshold = _as_finite_positive(threshold_value, name="mask_threshold")
    if mask_threshold > 1:
        raise RadiomicsProtocolError("mask_threshold must be finite and in (0, 1]")
    if target is None:
        mask_interpolator = "sitkNearestNeighbor"

    known = {
        "source_version",
        "target_spacing",
        "target_spacing_mm",
        "analysis_spacing",
        "resampling",
        "normalization",
        "normalization_scale",
        "normalization_scope",
        "bin_width",
        "discretisation",
        "discretisation_version",
        "interpolators",
        "image_interpolator",
        "mask_interpolator",
        "mask_threshold",
        "resegmentation",
        "processing_order",
        "protocol_version",
        "target_mode",
    }
    extra = {str(key): value for key, value in values.items() if key not in known}
    return RadiomicsProtocol(
        source_version=source_version,
        target_spacing_mm=target,
        normalization=normalized_name,
        normalization_scale=scale,
        normalization_scope=str(scope),
        bin_width=width,
        discretisation=discretisation,
        image_interpolator=image_interpolator,
        mask_interpolator=mask_interpolator,
        mask_threshold=mask_threshold,
        resegmentation=resegmentation,
        processing_order=order,
        version="shared-radiomics-grid-v5-post-interpolation-normalization" if order == "interpolate_then_normalize" else ("shared-radiomics-grid-v4-resegmentation" if resegmentation else ("shared-radiomics-grid-v3-linear-mask" if mask_interpolator == "sitkLinear" else PROTOCOL_VERSION)),
        extra=extra,
    )


# Short aliases make the interface easy for the 2D worker to import without
# coupling it to a particular historical function name.
resolve_protocol = resolve_radiomics_protocol


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def protocol_hash_payload(protocol: RadiomicsProtocol | Mapping[str, Any] | None) -> dict[str, Any]:
    resolved = (
        protocol
        if isinstance(protocol, RadiomicsProtocol)
        else resolve_radiomics_protocol(protocol)
    )
    return resolved.as_dict()


def _affine_from_spacing(spacing_xyz: tuple[float, float, float]) -> np.ndarray:
    affine = np.eye(4, dtype=np.float64)
    affine[0, 0], affine[1, 1], affine[2, 2] = spacing_xyz
    return affine


def _sitk_image_from_xyz(
    array_xyz: np.ndarray,
    spacing_xyz: tuple[float, float, float],
    affine: np.ndarray,
    *,
    pixel_type: int,
) -> sitk.Image:
    data = np.asarray(array_xyz)
    image = sitk.GetImageFromArray(np.asarray(data).transpose(2, 1, 0), isVector=False)
    image = sitk.Cast(image, pixel_type)
    matrix = np.asarray(affine, dtype=np.float64)[:3, :3]
    columns = np.linalg.norm(matrix, axis=0)
    if not np.all(np.isfinite(columns)) or np.any(columns <= 0):
        raise RadiomicsProtocolError("source affine has invalid voxel axes")
    # The spacing argument is authoritative for DICOM-derived arrays.  For a
    # NIfTI affine it is normally identical to the column norms; preserving
    # its direction and origin keeps the output NIfTI geometry meaningful.
    direction = matrix / columns
    if not np.allclose(direction.T @ direction, np.eye(3), rtol=1e-4, atol=1e-4):
        raise RadiomicsProtocolError("source affine contains unsupported non-orthogonal shear")
    image.SetSpacing(tuple(float(item) for item in spacing_xyz))
    image.SetDirection(tuple(float(item) for item in direction.reshape(-1)))
    image.SetOrigin(tuple(float(item) for item in np.asarray(affine, dtype=np.float64)[:3, 3]))
    return image


def _affine_from_sitk(image: sitk.Image) -> np.ndarray:
    spacing = np.asarray(image.GetSpacing(), dtype=np.float64)
    direction = np.asarray(image.GetDirection(), dtype=np.float64).reshape(3, 3)
    affine = np.eye(4, dtype=np.float64)
    affine[:3, :3] = direction * spacing[np.newaxis, :]
    affine[:3, 3] = np.asarray(image.GetOrigin(), dtype=np.float64)
    return affine


def _target_size(
    source_size_xyz: Sequence[int],
    source_spacing_xyz: tuple[float, float, float],
    target_spacing_xyz: tuple[float, float, float],
) -> tuple[int, int, int]:
    # Keep the physical location of the first and last source voxel.  This is
    # the same extent convention used by SimpleITK's resampling examples and
    # avoids shifting contours at the image boundary.
    return tuple(
        max(
            1,
            int(round((max(1, int(size)) - 1) * src / dst)) + 1,
        )
        for size, src, dst in zip(source_size_xyz, source_spacing_xyz, target_spacing_xyz)
    )  # type: ignore[return-value]


@dataclass(frozen=True)
class PreparedRadiomicsVolume:
    image_xyz: np.ndarray
    mask_xyz: np.ndarray
    source_spacing_xyz: tuple[float, float, float]
    target_spacing_xyz: tuple[float, float, float]
    source_shape_xyz: tuple[int, int, int]
    target_shape_xyz: tuple[int, int, int]
    source_affine: np.ndarray
    target_affine: np.ndarray
    protocol: RadiomicsProtocol
    provenance: dict[str, Any]
    intensity_mask_xyz: np.ndarray | None = None

    @property
    def spacing_xyz(self) -> tuple[float, float, float]:
        return self.target_spacing_xyz

    @property
    def affine(self) -> np.ndarray:
        return self.target_affine


def prepare_radiomics_volume(
    image_xyz: np.ndarray,
    mask_xyz: np.ndarray,
    source_spacing_xyz: Sequence[float],
    source_affine: np.ndarray | None = None,
    protocol: RadiomicsProtocol | Mapping[str, Any] | None = None,
    *,
    modality: str | None = None,
    allow_empty_intensity_mask: bool = False,
) -> PreparedRadiomicsVolume:
    """Prepare image and mask on one source/native or target grid.

    The mask uses nearest-neighbour or floating-point linear interpolation
    followed by inclusive thresholding. Both are resampled against the
    same reference grid, so their shape, affine, and physical extent remain
    aligned. Legacy tasks without range filtering normalize the native image
    before resampling. Explicit new-order research tasks interpolate the raw
    image, optionally filter the intensity mask, then normalize the full grid.
    Legacy range-filtering tasks retain their existing post-interpolation path.
    The returned mask_xyz always retains the morphological ROI.
    """

    image = np.asarray(image_xyz, dtype=np.float32)
    mask = np.asarray(mask_xyz) > 0.5
    if image.ndim != 3 or mask.ndim != 3:
        raise RadiomicsProtocolError("radiomics image and mask must both be 3D")
    if image.shape != mask.shape:
        raise RadiomicsProtocolError(f"image/mask shape mismatch: {image.shape} != {mask.shape}")
    source_spacing = _spacing(source_spacing_xyz, name="source_spacing")
    source_shape = tuple(int(value) for value in image.shape)
    source_affine_array = (
        np.asarray(source_affine, dtype=np.float64).copy()
        if source_affine is not None
        else _affine_from_spacing(source_spacing)
    )
    if source_affine_array.shape != (4, 4) or not np.all(np.isfinite(source_affine_array)):
        raise RadiomicsProtocolError("source_affine must be a finite 4x4 matrix")
    resolved = (
        protocol
        if isinstance(protocol, RadiomicsProtocol)
        else resolve_radiomics_protocol(protocol, modality=modality)
    )
    target_spacing = resolved.target_spacing_mm or source_spacing
    target_is_source = resolved.target_spacing_mm is None
    target_affine = source_affine_array.copy()

    # Legacy normalization uses the complete native volume before its grid. SITK
    # Normalize uses the sample standard deviation (N-1), which is also the
    # convention used by the PyRadiomics normalize path.  Keeping the native
    # pass here means workspace standard features and supplemental features
    # consume exactly the same prepared array after resampling.
    native_image = image
    normalization_stats: dict[str, Any] = {
        "method": resolved.normalization,
        "scope": resolved.normalization_scope,
        "scale": float(resolved.normalization_scale),
    }
    normalize_after_interpolation = bool(resolved.resegmentation) or resolved.processing_order == "interpolate_then_normalize"
    if resolved.normalization == "z-score" and not normalize_after_interpolation:
        if not np.isfinite(native_image).all():
            raise RadiomicsProtocolError("image contains non-finite values before normalization")
        native_sitk = _sitk_image_from_xyz(
            native_image,
            source_spacing,
            source_affine_array,
            pixel_type=sitk.sitkFloat32,
        )
        native_normalized_sitk = sitk.Normalize(native_sitk)
        native_image = np.asarray(
            sitk.GetArrayFromImage(native_normalized_sitk),
            dtype=np.float32,
        ).transpose(2, 1, 0)
        native_mean = float(np.mean(image))
        native_std = float(np.std(image, ddof=1)) if image.size > 1 else float("nan")
        if not math.isfinite(native_std) or native_std <= 0:
            raise RadiomicsProtocolError("image has no signal variation for normalization")
        native_image = (native_image * float(resolved.normalization_scale)).astype(np.float32)
        normalization_stats.update({"mean": native_mean, "std": native_std})

    if target_is_source:
        prepared_image = native_image.copy()
        prepared_mask = mask.copy()
    else:
        source_sitk = _sitk_image_from_xyz(
            native_image,
            source_spacing,
            source_affine_array,
            pixel_type=sitk.sitkFloat32,
        )
        linear_mask = resolved.mask_interpolator == "sitkLinear"
        mask_pixel_type = sitk.sitkFloat32 if linear_mask else sitk.sitkUInt8
        source_mask_sitk = _sitk_image_from_xyz(
            mask.astype(np.float32 if linear_mask else np.uint8),
            source_spacing,
            source_affine_array,
            pixel_type=mask_pixel_type,
        )
        size_xyz = _target_size(source_shape, source_spacing, target_spacing)
        if math.prod(size_xyz) > MAX_ANALYSIS_VOXELS:
            raise RadiomicsProtocolError("目标间距生成的分析网格过大，请增大重采样间距")
        reference = sitk.Image([int(value) for value in size_xyz], sitk.sitkFloat32)
        reference.SetSpacing(tuple(float(value) for value in target_spacing))
        reference.SetDirection(source_sitk.GetDirection())
        reference.SetOrigin(source_sitk.GetOrigin())
        image_interpolator = {
            "sitkBSpline": sitk.sitkBSpline,
            "sitkLinear": sitk.sitkLinear,
            "sitkNearestNeighbor": sitk.sitkNearestNeighbor,
        }[resolved.image_interpolator]
        prepared_sitk = sitk.Resample(
            source_sitk,
            reference,
            sitk.Transform(),
            image_interpolator,
            0.0,
            sitk.sitkFloat32,
        )
        prepared_mask_sitk = sitk.Resample(
            source_mask_sitk,
            reference,
            sitk.Transform(),
            sitk.sitkLinear if linear_mask else sitk.sitkNearestNeighbor,
            0.0,
            mask_pixel_type,
        )
        prepared_image = np.asarray(sitk.GetArrayFromImage(prepared_sitk), dtype=np.float32).transpose(2, 1, 0)
        mask_values = sitk.GetArrayFromImage(prepared_mask_sitk).transpose(2, 1, 0)
        prepared_mask = mask_values >= resolved.mask_threshold if linear_mask else mask_values > 0
        target_affine = _affine_from_sitk(prepared_sitk)

    if not np.any(prepared_mask):
        raise RadiomicsProtocolError("mask is empty on the analysis grid")
    if not np.isfinite(prepared_image).all():
        raise RadiomicsProtocolError("image contains non-finite values on the analysis grid")

    intensity_mask = prepared_mask.copy()
    resegmentation_stats = None
    if resolved.resegmentation:
        lower, upper = resolved.resegmentation["lower"], resolved.resegmentation["upper"]
        intensity_mask &= (prepared_image >= lower) & (prepared_image <= upper)
        before, after = int(prepared_mask.sum()), int(intensity_mask.sum())
        resegmentation_stats = {**resolved.resegmentation, "before_voxels": before,
                                "after_voxels": after, "retained_fraction": after / before,
                                "intensity_stage": "analysis_grid_before_normalization"}
        if not after and not allow_empty_intensity_mask:
            error = RadiomicsProtocolError(f"empty_intensity_mask: 重分割后强度掩膜为空（{before} → 0 体素，保留比例 0）")
            error.resegmentation_stats = resegmentation_stats
            raise error
    if normalize_after_interpolation:
        if resolved.normalization == "z-score":
            mean = float(np.mean(prepared_image, dtype=np.float64))
            std = float(np.std(prepared_image, ddof=1, dtype=np.float64)) if prepared_image.size > 1 else float("nan")
            if not math.isfinite(std) or std <= 0:
                raise RadiomicsProtocolError("image has no signal variation for normalization")
            prepared_image = ((prepared_image.astype(np.float64) - mean) / std * resolved.normalization_scale).astype(np.float32)
            normalization_stats.update({"mean": mean, "std": std, "standard_deviation_ddof": 1})
            normalization_stats.update({"includes_roi_exterior": True, "includes_resampling_padding": True})
            if not np.isfinite(prepared_image).all():
                raise RadiomicsProtocolError("image contains non-finite values after normalization")

    target_spacing_actual = tuple(float(item) for item in target_spacing)
    target_shape = tuple(int(value) for value in prepared_image.shape)
    provenance = {
        "protocol_version": resolved.version,
        "protocol_hash": resolved.hash(),
        "target_mode": "native" if target_is_source else "resampled",
        "resampled": bool(not target_is_source),
        "source_spacing_mm": [float(item) for item in source_spacing],
        "target_spacing_mm": [float(item) for item in target_spacing_actual],
        "source_spacing_xyz": [float(item) for item in source_spacing],
        "target_spacing_xyz": [float(item) for item in target_spacing_actual],
        "actual_source_spacing_mm": [float(item) for item in source_spacing],
        "actual_target_spacing_mm": [float(item) for item in target_spacing_actual],
        "source_shape_xyz": [int(item) for item in source_shape],
        "target_shape_xyz": [int(item) for item in target_shape],
        "source_affine": source_affine_array.tolist(),
        "target_affine": target_affine.tolist(),
        "interpolators": {
            "image": resolved.image_interpolator if not target_is_source else "none",
            "mask": resolved.mask_interpolator if not target_is_source else "none",
        },
        "normalization": normalization_stats,
        "bin_width": float(resolved.bin_width),
        "mask_threshold": resolved.mask_threshold if not target_is_source and resolved.mask_interpolator == "sitkLinear" else None,
        "mask_source_voxels": int(np.count_nonzero(mask)),
        "mask_analysis_voxels": int(np.count_nonzero(prepared_mask)),
        "masks": {
            "morphological": {
                "source": "analysis_grid_roi",
                "voxels": int(np.count_nonzero(prepared_mask)),
            },
            "intensity": {
                "source": "range_resegmentation" if resolved.resegmentation else "analysis_grid_roi",
                "voxels": int(np.count_nonzero(intensity_mask)),
            },
        },
        "resampling": "none" if target_is_source else [float(item) for item in target_spacing_actual],
    }
    if resegmentation_stats is not None:
        provenance["resegmentation"] = resegmentation_stats
        provenance["processing_order"] = "interpolate-resegment-normalize-discretize-calculate-v1"
    if resolved.processing_order == "interpolate_then_normalize":
        provenance["processing_order"] = resolved.processing_order
    return PreparedRadiomicsVolume(
        image_xyz=prepared_image,
        mask_xyz=prepared_mask,
        source_spacing_xyz=source_spacing,
        target_spacing_xyz=target_spacing_actual,
        source_shape_xyz=source_shape,
        target_shape_xyz=target_shape,
        source_affine=source_affine_array,
        target_affine=target_affine,
        protocol=resolved,
        provenance=provenance,
        intensity_mask_xyz=intensity_mask,
    )


prepare_analysis_volume = prepare_radiomics_volume
prepare_volume = prepare_radiomics_volume
