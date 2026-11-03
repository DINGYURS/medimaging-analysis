"""
DICOM & NIfTI I/O Module for Medical Imaging Analysis Platform.
Handles reading DICOM series, extracting metadata, spacing/orientation alignment,
and exporting standard 3D NIfTI volumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

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
