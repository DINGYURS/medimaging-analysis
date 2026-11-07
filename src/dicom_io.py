"""
DICOM & NIfTI I/O Module for Medical Imaging Analysis Platform.
Handles reading DICOM series, extracting metadata, spacing/orientation alignment,
and exporting standard 3D NIfTI volumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from functools import lru_cache

import nibabel as nib
import numpy as np
import pydicom
import SimpleITK as sitk


def load_dicom_series(
    dicom_dir: str | Path,
    expected_series_id: Optional[str] = None
) -> Tuple[sitk.Image, Dict[str, Any]]:
    """
    Load a complete 3D DICOM series from directory with correct spatial geometry and HU units.
    
    Returns:
        image: SimpleITK 3D Image in LPS coordinate system.
        metadata: Extracted patient and acquisition metadata.
    """
    dicom_dir = Path(dicom_dir)
    if not dicom_dir.is_dir():
        raise FileNotFoundError(f"DICOM directory not found: {dicom_dir}")

    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(str(dicom_dir))
    if not series_ids:
        raise ValueError(f"No valid DICOM series found in {dicom_dir}")

    target_series_id = expected_series_id if expected_series_id in series_ids else series_ids[0]
    file_names = reader.GetGDCMSeriesFileNames(str(dicom_dir), target_series_id)
    if not file_names:
        raise ValueError(f"No file names found for series {target_series_id}")

    reader.SetFileNames(file_names)
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()
    image = reader.Execute()

    # Parse representative DICOM header metadata
    sample_file = file_names[0]
    ds = pydicom.dcmread(sample_file, stop_before_pixels=True)

    metadata: Dict[str, Any] = {
        "series_instance_uid": target_series_id,
        "study_instance_uid": getattr(ds, "StudyInstanceUID", "Unknown"),
        "patient_id": getattr(ds, "PatientID", "Unknown"),
        "modality": getattr(ds, "Modality", "CT"),
        "study_description": getattr(ds, "StudyDescription", ""),
        "series_description": getattr(ds, "SeriesDescription", ""),
        "slice_count": len(file_names),
        "rows": getattr(ds, "Rows", None),
        "columns": getattr(ds, "Columns", None),
        "slice_thickness_mm": float(getattr(ds, "SliceThickness", 0.0) or 0.0),
        "spacing_between_slices_mm": float(getattr(ds, "SpacingBetweenSlices", 0.0) or 0.0),
        "pixel_spacing": [float(x) for x in getattr(ds, "PixelSpacing", [1.0, 1.0])],
        "window_center": getattr(ds, "WindowCenter", None),
        "window_width": getattr(ds, "WindowWidth", None),
        "rescale_intercept": float(getattr(ds, "RescaleIntercept", 0.0) or 0.0),
        "rescale_slope": float(getattr(ds, "RescaleSlope", 1.0) or 1.0),
        "volume_size_xyz": list(image.GetSize()),
        "volume_spacing_xyz": list(image.GetSpacing()),
        "volume_origin_xyz": list(image.GetOrigin()),
        "volume_direction": list(image.GetDirection()),
    }

    # Normalize window level if stored as multi-value
    if isinstance(metadata["window_center"], pydicom.multival.MultiValue):
        metadata["window_center"] = float(metadata["window_center"][0])
    elif metadata["window_center"] is not None:
        metadata["window_center"] = float(metadata["window_center"])

    if isinstance(metadata["window_width"], pydicom.multival.MultiValue):
        metadata["window_width"] = float(metadata["window_width"][0])
    elif metadata["window_width"] is not None:
        metadata["window_width"] = float(metadata["window_width"])

    return image, metadata


def save_image_to_nifti(image: sitk.Image, output_path: str | Path) -> Path:
    """Save SimpleITK Image as standard compressed NIfTI (.nii.gz)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(output_path), True)
    return output_path


def load_nifti_image(nifti_path: str | Path) -> Tuple[sitk.Image, Dict[str, Any]]:
    """Load a 3D NIfTI image and return SimpleITK image with basic spatial metadata."""
    nifti_path = Path(nifti_path)
    if not nifti_path.exists():
        raise FileNotFoundError(f"NIfTI file not found: {nifti_path}")

    image = sitk.ReadImage(str(nifti_path))
    metadata = {
        "file_name": nifti_path.name,
        "volume_size_xyz": list(image.GetSize()),
        "volume_spacing_xyz": list(image.GetSpacing()),
        "volume_origin_xyz": list(image.GetOrigin()),
        "volume_direction": list(image.GetDirection()),
    }
    return image, metadata


def _dicom_hu_evidence(ds):
    """Resolve classic CT output units conservatively from DICOM PS3.3 C.8.2."""
    if str(getattr(ds, 'SOPClassUID', '')) != '1.2.840.10008.5.1.4.1.1.2' or str(getattr(ds, 'Modality', '')) != 'CT':
        return None
    if int(getattr(ds, 'NumberOfFrames', 1)) != 1 or int(getattr(ds, 'SamplesPerPixel', 1)) != 1:
        return None
    if any(key in ds for key in ['ModalityLUTSequence', 'RealWorldValueMappingSequence', 'PixelValueTransformationSequence']):
        return None
    try:
        slope, intercept = float(ds.RescaleSlope), float(ds.RescaleIntercept)
        if not np.isfinite([slope, intercept]).all() or slope == 0:
            return None
    except (AttributeError, ValueError, TypeError):
        return None
    unit = str(getattr(ds, 'RescaleType', '')).strip().upper()
    if unit:
        return 'dicom_rescale_type' if unit == 'HU' else None
    image_type = list(getattr(ds, 'ImageType', []))
    if len(image_type) >= 3 and image_type[0] == 'ORIGINAL' and image_type[2] != 'LOCALIZER' and str(getattr(ds, 'MultienergyCTAcquisition', 'NO')).upper() == 'NO':
        return 'dicom_original_ct_standard'
    return None


@lru_cache(maxsize=16)
def _verify_hu_pixels(file_signatures, volume_signature):
    volume = sitk.GetArrayFromImage(sitk.ReadImage(volume_signature[0]))
    if volume.ndim != 3 or volume.shape[0] != len(file_signatures):
        return {'unit': None, 'reason': 'volume_depth_mismatch'}
    sources = set()
    for index, signature in enumerate(file_signatures):
        ds = pydicom.dcmread(signature[0])
        source = _dicom_hu_evidence(ds)
        if source is None:
            return {'unit': None, 'reason': 'insufficient_or_unsupported_dicom_evidence'}
        expected = ds.pixel_array.astype(np.float64) * float(ds.RescaleSlope) + float(ds.RescaleIntercept)
        if expected.shape != volume[index].shape or not np.allclose(expected, volume[index], rtol=0, atol=1e-4):
            return {'unit': None, 'reason': 'stored_values_do_not_match_dicom_rescale'}
        sources.add(source)
    return {'unit': 'HU', 'source': '+'.join(sorted(sources)), 'verified_slices': len(file_signatures)}


def verify_dicom_intensity_unit(dicom_dir, nifti_path, series_uid=None):
    """Read-only verification against originals; cache invalidates on source file changes."""
    try:
        if not dicom_dir or not nifti_path:
            return {'unit': None, 'reason': 'original_files_unavailable'}
        ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(str(dicom_dir)) or []
        if series_uid:
            if series_uid not in ids:
                return {'unit': None, 'reason': 'source_series_unavailable'}
            chosen = series_uid
        elif len(ids) == 1:
            chosen = ids[0]
        else:
            return {'unit': None, 'reason': 'ambiguous_source_series'}
        files = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(dicom_dir), chosen)
        if not files:
            return {'unit': None, 'reason': 'original_files_unavailable'}
        def signature(path):
            stat = Path(path).stat()
            return (str(path), stat.st_size, stat.st_mtime_ns)
        return dict(_verify_hu_pixels(tuple(signature(f) for f in files), signature(nifti_path)))
    except Exception:
        return {'unit': None, 'reason': 'original_verification_unavailable'}
