"""Server-owned file/ROI inspection. All arrays use XYZ and RAS millimetres."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import hashlib, json
import nibabel as nib
import numpy as np
import SimpleITK as sitk
import pydicom
from scipy import ndimage as ndi
from app.core.config import settings
from .physical import checked_affine, nifti_geometry, dicom_geometry, frame_transform, intensity_evidence

def digest_file(path):
    p = Path(path).resolve()
    if not p.is_relative_to(Path(settings.BASE_DIR).resolve()) or not p.is_file():
        raise ValueError('源影像或掩膜文件不存在，或不在项目存储范围内')
    before = p.stat()
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda : f.read(1024 * 1024), b''):
            h.update(chunk)
    after = p.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('源文件在读取中变化，请重新确认')
    return {'path': str(p), 'sha256': h.hexdigest(), 'size': after.st_size}

def source_files(resource):
    study = resource['study']
    paths = [study['nifti_path']] + [r['mask_nifti_path'] for r in resource['lesions']]
    directory = study.get('dicom_dir')
    dicoms = []
    if directory and Path(directory).is_dir():
        ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(directory) or []
        uid = (study.get('metadata_json') or {}).get('series_instance_uid')
        if uid not in ids:
            if len(ids) != 1:
                raise ValueError('无法唯一匹配当前检查的原始DICOM序列')
            uid = ids[0]
        dicoms = list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(directory, uid))
    paths += dicoms
    return (sorted(set(paths)), dicoms)

def read_source(resource, source_version):
    study = resource['study']
    (_, dicoms) = source_files(resource)
    original = nib.load(study['nifti_path'])
    if len(original.shape) != 3 or np.prod(original.shape) > 256000000:
        raise ValueError('影像尺寸超出当前三维计算资源限制')
    image = np.asarray(original.get_fdata(dtype=np.float32))
    affine = np.array(original.affine)
    if image.ndim != 3:
        raise ValueError('仅支持三维影像')
    (affine, spacing) = checked_affine(affine)
    evidence = {'unit': None, 'evidence': 'unknown'}
    facts = {'source_version': source_version, 'coordinate_system': 'RAS', 'spatial_unit': 'mm'}
    if source_version == 'physical-v1' and dicoms:
        datasets = {}
        refs = []
        frames = []
        transforms = []
        for path in dicoms:
            ds = pydicom.dcmread(path)
            datasets[path] = ds
            for index in range(int(getattr(ds, 'NumberOfFrames', 1))):
                refs.append(SimpleNamespace(image_file=SimpleNamespace(id=path), frame_index=index))
        (shape, source_affine, spacing) = dicom_geometry(refs, datasets)
        if tuple(shape) != image.shape or not np.allclose(source_affine, affine, atol=0.001, rtol=1e-05):
            raise ValueError('原始DICOM空间与当前影像/分割网格不一致，不能自动重排ROI')
        for ref in refs:
            ds = datasets[ref.image_file.id]
            pixels = np.asarray(ds.pixel_array)
            if int(getattr(ds, 'SamplesPerPixel', 1)) != 1:
                raise ValueError('仅支持单通道影像')
            if pixels.ndim == 3:
                pixels = pixels[ref.frame_index]
            if pixels.shape != (shape[1], shape[0]):
                raise ValueError('原始DICOM像素尺寸不一致')
            transform = frame_transform(ds, ref.frame_index)
            frames.append(pixels.astype(np.float64) * transform['slope'] + transform['intercept'])
            transforms.append(transform)
        image = np.stack(frames).transpose(2, 1, 0).astype(np.float32)
        affine = source_affine
        evidence = intensity_evidence(transforms)
        facts['affine_source'] = 'dicom_patient_geometry'
    elif source_version == 'physical-v1':
        (affine, spacing, facts_nifti) = nifti_geometry(original)
        facts.update(facts_nifti)
    elif dicoms:
        from src.dicom_io import verify_dicom_intensity_unit, _verify_hu_pixels
        _verify_hu_pixels.cache_clear()
        evidence = verify_dicom_intensity_unit(study['dicom_dir'], study['nifti_path'], (study.get('metadata_json') or {}).get('series_instance_uid'))
        evidence['evidence'] = 'dicom_pixels_and_metadata' if evidence.get('unit') else 'unknown'
    if dicoms:
        header = pydicom.dcmread(dicoms[0], stop_before_pixels=True, specific_tags=['BodyPartExamined','SeriesDescription'])
        facts['anatomy'] = {'body_part': str(getattr(header,'BodyPartExamined','') or ''), 'series_description': str(getattr(header,'SeriesDescription','') or '')}
    if not np.isfinite(image).all():
        raise ValueError('源影像含非有限强度')
    return (image, affine, spacing, evidence, facts)

def inspect(resource, source_version, load_arrays=False):
    (paths, _) = source_files(resource)
    before = [digest_file(p) for p in paths]
    (image, affine, spacing, evidence, facts) = read_source(resource, source_version)
    masks = {}
    mapped = []
    excluded = []
    study = resource["study"]
    metadata = study.get("metadata_json") or {}
    native_mri = (str(study.get("modality", "")).upper() in {"MR", "MRI"}
                  and metadata.get("import_mode") == "per_series_native")
    used = set()
    rois = []
    for row in resource['lesions']:
        path = row['mask_nifti_path']
        if path not in masks:
            volume = nib.load(path)
            mask_affine = np.array(volume.affine)
            if source_version == 'physical-v1':
                (mask_affine, _, _) = nifti_geometry(volume)
            if volume.shape != image.shape or not np.allclose(mask_affine, affine, atol=0.001, rtol=1e-05):
                raise ValueError('掩膜与源影像空间不一致，未自动移动或重排ROI')
            data = np.asarray(volume.get_fdata())
            (labels, count) = ndi.label(data > 0)
            if not np.isfinite(data).all():
                raise ValueError('掩膜含非有限值')
            comps = []
            for label in range(1, count + 1):
                roi = labels == label
                xyz = np.array(ndi.center_of_mass(roi))
                ras = (affine @ np.r_[xyz, 1])[:3]
                lps = ras * np.array([-1, -1, 1])
                vol = float(roi.sum() * np.prod(spacing))
                comps.append((label, lps, vol))
            masks[path] = (labels, comps)
        (labels, comps) = masks[path]
        matches = [label for (label, center, vol) in comps if np.allclose(center, row['center'], atol=0.021, rtol=0) and abs(vol - row['volume_mm3']) <= max(0.021, vol * 1e-05)]
        if len(matches) != 1 or (path, matches[0]) in used:
            raise ValueError('病灶物理中心/体积无法唯一对应当前掩膜，需核对分析结果')
        label = matches[0]
        used.add((path, label))
        roi = labels == label
        if np.count_nonzero(np.any(roi, axis=(0, 1))) < 2:
            if not native_mri:
                raise ValueError('ROI至少需要覆盖两个切层')
            excluded.append({
                "lesion_id": row["id"],
                "lesion_index": row["lesion_index"],
                "reason": "ROI 至少需要覆盖两个切层",
            })
            continue
        info = {'lesion_id': row['id'], 'lesion_index': row['lesion_index'], 'voxels': int(roi.sum()), 'mask_digest': hashlib.sha256(roi.tobytes()).hexdigest()}
        mapped.append(info)
        if load_arrays:
            rois.append((row, roi))
    after = [digest_file(p) for p in paths]
    if before != after:
        raise ValueError('输入内容在确认中发生变化，请重试')
    snapshot = {'resource': resource, 'files': after, 'mapping': mapped, 'source': facts, 'intensity': evidence}
    if excluded:
        if not mapped:
            raise ValueError("MRI 候选病灶均不足两个切层，暂无可计算的 3D ROI")
        snapshot["excluded_lesions"] = excluded
    if load_arrays:
        return (snapshot, (image, affine, spacing, evidence, rois))
    return snapshot
