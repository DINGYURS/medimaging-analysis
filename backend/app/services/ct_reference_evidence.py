"""Local evidence for CT lung-nodule references. This module makes no network calls."""
from pathlib import Path

import numpy as np
import pydicom
import SimpleITK as sitk
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError
from scipy.spatial.distance import pdist, squareform

LOBE_LABELS = {10: "左肺上叶", 11: "左肺下叶", 12: "右肺上叶", 13: "右肺中叶", 14: "右肺下叶"}
MIN_LOBE_OVERLAP = 0.8
MIN_CANDIDATE_VOLUME = 25.0


class EvidenceUnavailable(ValueError):
    pass


def original_slices(image, dicom_dir, series_uid):
    """Bind only a verifiable original single-frame stack to NIfTI planes."""
    if not dicom_dir or not series_uid or not Path(dicom_dir).is_dir():
        return [], False
    try:
        names = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(dicom_dir), str(series_uid))
    except RuntimeError:
        return [], False
    if not names:
        return [], False
    fields = [
        "Modality", "BodyPartExamined", "ProtocolName", "SeriesDescription", "StudyDescription",
        "SeriesInstanceUID", "SOPInstanceUID", "InstanceNumber", "NumberOfFrames",
        "ImagePositionPatient", "ImageOrientationPatient", "PixelSpacing", "Rows", "Columns",
    ]
    try:
        first = pydicom.dcmread(names[0], stop_before_pixels=True, specific_tags=fields)
    except (ValueError, OSError):
        return [], False
    if first.get("Modality") != "CT" or str(first.get("SeriesInstanceUID", "")) != series_uid:
        return [], False
    description = " ".join(str(first.get(key, "")) for key in fields[1:5]).upper()
    is_chest = any(term in description for term in ("CHEST", "THORAX", "THORACIC", "LUNG", "LIDC", "胸", "肺"))
    if len(names) != image.GetDepth():
        return [], is_chest

    direction = np.array(image.GetDirection()).reshape(3, 3)
    expected_orientation = np.r_[direction[:, 0], direction[:, 1]]
    expected_spacing = list(image.GetSpacing()[:2])[::-1]
    anchors = []
    try:
        for index, name in enumerate(names):
            ds = first if index == 0 else pydicom.dcmread(name, stop_before_pixels=True, specific_tags=fields)
            if (ds.Modality != "CT" or str(ds.SeriesInstanceUID) != series_uid
                    or int(ds.get("NumberOfFrames", 1)) != 1
                    or (int(ds.Columns), int(ds.Rows)) != image.GetSize()[:2]
                    or not np.allclose(ds.ImageOrientationPatient, expected_orientation, rtol=0, atol=1e-4)
                    or not np.allclose(ds.PixelSpacing, expected_spacing, rtol=0, atol=1e-4)
                    or not np.allclose(ds.ImagePositionPatient, image.TransformIndexToPhysicalPoint((0, 0, index)),
                                       rtol=0, atol=0.05)):
                return [], is_chest
            sop_uid = str(ds.SOPInstanceUID)
            if not sop_uid:
                return [], is_chest
            anchors.append({"slice": index + 1, "sop_instance_uid": sop_uid, "series_instance_uid": series_uid})
    except (AttributeError, ValueError, TypeError, OSError):
        return [], is_chest
    if len({anchor["sop_instance_uid"] for anchor in anchors}) != len(anchors):
        return [], is_chest
    return anchors, is_chest


def axial_measurement(roi, bounds, spacing):
    """Largest distance between occupied voxel centers in one axial plane."""
    best = None
    for index, plane in enumerate(roi):
        points = np.argwhere(plane)
        if len(points) < 2:
            continue
        if len(points) > 2:
            try:
                points = points[ConvexHull(points).vertices]
            except QhullError:
                axis = int(np.argmax(np.ptp(points, axis=0)))
                points = points[[points[:, axis].argmin(), points[:, axis].argmax()]]
        distances = squareform(pdist(points[:, ::-1] * spacing))
        first, second = np.unravel_index(distances.argmax(), distances.shape)
        distance = float(distances[first, second])
        if best is None or distance > best["raw_mm"]:
            endpoints = points[[first, second], ::-1] + [bounds[2].start, bounds[1].start]
            best = {
                "raw_mm": distance,
                "diameter_mm": round(distance, 1),
                "slice_index": bounds[0].start + index,
                "endpoints_xy": endpoints.tolist(),
            }
    return best


def lobe_location(lobes, roi):
    if lobes is None:
        return None, None
    counts = np.bincount(lobes[roi], minlength=15)
    total = int(roi.sum())
    label = max(LOBE_LABELS, key=lambda item: counts[item])
    side = None
    for text, ids in (("左肺", (10, 11)), ("右肺", (12, 13, 14))):
        if sum(counts[item] for item in ids) >= MIN_LOBE_OVERLAP * total:
            side = text
    location = LOBE_LABELS[label] if counts[label] >= MIN_LOBE_OVERLAP * total else None
    return location, side


def candidate_components(image, mask, lesions):
    """Resolve task candidates using the same component numbering and physical centers."""
    if image.GetDimension() != 3 or not image.IsSameImageGeometryAs(mask, 1e-4, 1e-5):
        raise EvidenceUnavailable("候选掩膜与所选 CT 序列的物理空间不一致。")
    values = sitk.GetArrayViewFromImage(mask)
    if not np.isfinite(values).all():
        raise EvidenceUnavailable("候选掩膜含无效体素。")
    labels, count = ndimage.label(values > 0)
    counts = np.zeros(count + 1, dtype=np.int64)
    for slab in labels:
        counts += np.bincount(slab.ravel(), minlength=count + 1)
    voxel_volume = float(np.prod(image.GetSpacing()))
    keys = [label for label in range(1, count + 1) if counts[label] * voxel_volume >= MIN_CANDIDATE_VOLUME]
    keys.sort(key=lambda label: round(float(counts[label]) * voxel_volume, 2), reverse=True)
    if len(keys) != len(lesions):
        raise EvidenceUnavailable("候选掩膜与已保存的病灶清单不一致。")
    bounds = ndimage.find_objects(labels)
    for index, (label, saved) in enumerate(zip(keys, lesions), start=1):
        region = bounds[label - 1]
        roi = labels[region] == label
        center = np.argwhere(roi).mean(axis=0)[::-1] + [item.start for item in region[::-1]]
        world = image.TransformContinuousIndexToPhysicalPoint(center.tolist())
        if index != saved[0] or not np.allclose(world, saved[1:], rtol=0, atol=0.04):
            raise EvidenceUnavailable("候选掩膜与已保存的病灶定位不一致。")
        yield saved, region, roi


def candidates(image, mask, lobes, lesions, anchors):
    lobe_array = None
    if lobes is not None and image.IsSameImageGeometryAs(lobes, 1e-4, 1e-5):
        computed = sitk.GetArrayViewFromImage(lobes)
        if np.all(np.isin(computed, [0, *LOBE_LABELS])) and np.any(computed):
            lobe_array = computed
    direction = np.array(image.GetDirection()).reshape(3, 3)
    axial = abs(direction[2, 2]) >= 0.999
    findings = []
    for saved, region, roi in candidate_components(image, mask, lesions):
        index = saved[0]
        lobe, side = lobe_location(lobe_array[region] if lobe_array is not None else None, roi)
        measurement = axial_measurement(roi, region, image.GetSpacing()[:2]) if axial else None
        anchor = None
        if measurement is not None and anchors:
            anchor = {**anchors[measurement["slice_index"]], "endpoints_xy": measurement["endpoints_xy"]}
        findings.append({
            "id": f"L{index}",
            "lesion_index": index,
            "lobe": lobe,
            "side": side,
            "diameter_mm": measurement["diameter_mm"] if measurement else None,
            "diameter_raw_mm": measurement["raw_mm"] if measurement else None,
            "anchor": anchor,
        })
    return findings, lobe_array is not None
