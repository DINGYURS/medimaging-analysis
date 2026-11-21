import asyncio
import hashlib
import shutil
import tempfile
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pydicom
import SimpleITK as sitk
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Patient, Study, User
from app.services.liver_mri_series import (
    is_liver_mri_group,
    patient_identity,
    select_liver_mri_series,
    study_uid,
)
from src.dicom_io import load_dicom_series, save_image_to_nifti


MRI_ROLE_LABELS = {
    "t2": "T2",
    "t1_pre": "T1 平扫",
    "arterial_early": "早期动脉期",
    "portal": "门脉期",
    "delayed": "延迟期",
}


def _unpack(upload: Path, filename: str, destination: Path) -> None:
    destination.mkdir()
    if not zipfile.is_zipfile(upload):
        shutil.copyfile(upload, destination / filename)
        return
    with zipfile.ZipFile(upload) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("ZIP 含无效路径")
        archive.extractall(destination)


def _read_mri_selections(directory: Path):
    groups = defaultdict(list)
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__MACOSX" in path.parts:
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except (pydicom.errors.InvalidDicomError, OSError):
            continue
        if str(getattr(ds, "Modality", "")).upper() in {"MR", "MRI"}:
            groups[(study_uid(ds), patient_identity(ds))].append(SimpleNamespace(path=path, dataset=ds))
    liver_groups = [entries for entries in groups.values() if is_liver_mri_group(entries)]
    if not liver_groups:
        return ()
    if len(liver_groups) != 1 or len(groups) != 1:
        raise ValueError("每个肝脏 MRI ZIP 只可包含一次检查")
    return select_liver_mri_series(liver_groups[0])


def _read_single(directory: Path):
    for candidate in [directory, *sorted(p for p in directory.rglob("*") if p.is_dir())]:
        try:
            image, metadata = load_dicom_series(candidate)
        except (RuntimeError, ValueError):
            continue
        if image.GetDimension() == 3 and metadata["slice_count"] > 0:
            return candidate, image, metadata
    raise ValueError("上传文件中未能解析出有效的 DICOM 影像序列")


def _parse_mri(selection, staging: Path, doctor_id: int):
    directory = staging / f"series_{selection.channel}"
    directory.mkdir()
    for index, entry in enumerate(selection.entries):
        shutil.copyfile(entry.path, directory / f"{index:05d}.dcm")
    image, metadata = load_dicom_series(directory, selection.series_uid)
    if image.GetDimension() != 3 or image.GetSize()[2] < 2:
        raise ValueError("肝脏 MRI 序列必须为至少两层的三维影像")
    if metadata["series_instance_uid"] != selection.series_uid:
        raise ValueError("解析的 DICOM 序列与所选序列不一致")
    metadata.update(
        modality="MR",
        source_study_instance_uid=selection.study_uid,
        body_part="LIVER",
        organ="LIVER",
        sequence_role=selection.role,
        sequence_label=MRI_ROLE_LABELS[selection.role],
        nnunet_channel=selection.channel,
        import_mode="per_series_native",
    )
    if not metadata.get("window_width") or metadata.get("window_center") is None:
        voxels = sitk.GetArrayViewFromImage(image).reshape(-1)
        sampled = voxels[::max(1, voxels.size // 100_000)]
        sampled = sampled[np.isfinite(sampled)]
        if sampled.size:
            low, high = np.percentile(sampled, [1, 99])
            metadata.update(window_width=max(1.0, float(high - low)), window_center=float((low + high) / 2.0))
    # Existing tasks and viewers bind one native volume to a Study record.
    # The original StudyInstanceUID remains in metadata for grouping.
    key = f"{doctor_id}:{selection.study_uid}:{selection.series_uid}"
    return directory, image, metadata, "MR-" + hashlib.sha256(key.encode()).hexdigest()


def _parse_upload(upload: Path, filename: str, staging: Path, doctor_id: int):
    source = staging / "extracted"
    _unpack(upload, filename, source)
    selected = _read_mri_selections(source)
    if selected:
        return [_parse_mri(series, staging, doctor_id) for series in selected]
    directory, image, metadata = _read_single(source)
    uid = metadata.get("study_instance_uid") or f"STUDY_{uuid.uuid4().hex}"
    return [(directory, image, metadata, uid)]


def _study_date(metadata):
    value = str(metadata.get("study_date") or "").strip()
    try:
        return datetime.strptime(value, "%Y%m%d") if len(value) == 8 else None
    except ValueError:
        return None


async def process_and_save_uploaded_dicom(file: UploadFile, doctor: User, db: AsyncSession) -> Study:
    storage = Path(settings.STORAGE_DIR)
    storage.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="upload_", dir=storage) as temp:
        staging = Path(temp)
        upload = staging / "upload"
        filename = Path((file.filename or "image.dcm").replace(chr(92), "/")).name
        if filename in {"", ".", ".."}:
            raise HTTPException(400, "上传文件名无效")
        with upload.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                target.write(chunk)
        try:
            entries = await asyncio.to_thread(_parse_upload, upload, filename, staging, doctor.id)
        except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, str(exc)) from exc

        created_paths = []
        imported = []
        try:
            for directory, image, metadata, uid in entries:
                existing = await db.scalar(select(Study).where(Study.study_instance_uid == uid))
                if existing is not None:
                    if uid.startswith("MR-") and existing.doctor_id == doctor.id:
                        imported.append(existing)
                        continue
                    raise HTTPException(409, "该 DICOM 检查已导入")

                patient_code = metadata.get("patient_id") or f"P_{uuid.uuid4().hex[:8]}"
                patient = await db.scalar(select(Patient).where(
                    Patient.doctor_id == doctor.id, Patient.patient_id == patient_code
                ).order_by(Patient.id).limit(1))
                if patient is None:
                    patient = Patient(
                        doctor_id=doctor.id,
                        patient_id=patient_code,
                        name=metadata.get("patient_name") or f"患者_{patient_code}",
                        gender=metadata.get("patient_sex") or "未知",
                        age=metadata.get("patient_age") or None,
                    )
                    db.add(patient)
                    await db.flush()

                description = (metadata.get("study_description") or metadata.get("series_description") or "").strip()
                if metadata.get("sequence_label"):
                    description = f"{description[:220] or '肝脏 MRI'} · {metadata['sequence_label']}"
                study = Study(
                    patient_id=patient.id, doctor_id=doctor.id, study_instance_uid=uid,
                    modality=metadata.get("modality", "CT"), study_description=description or "无",
                    study_date=_study_date(metadata), slice_count=image.GetSize()[2], metadata_json=metadata,
                )
                db.add(study)
                await db.flush()
                volume = Path(settings.NIFTI_DIR) / f"study_{study.id}_volume.nii.gz"
                dicoms = Path(settings.DICOM_DIR) / f"study_{study.id}"
                volume.parent.mkdir(parents=True, exist_ok=True)
                dicoms.parent.mkdir(parents=True, exist_ok=True)
                if volume.exists() or dicoms.exists():
                    raise RuntimeError("分配的检查存储路径已存在")
                created_paths.append((volume, dicoms))
                await asyncio.to_thread(save_image_to_nifti, image, volume)
                await asyncio.to_thread(shutil.copytree, directory, dicoms)
                study.nifti_path, study.dicom_dir = str(volume), str(dicoms)
                imported.append(study)
            await db.commit()
        except BaseException as exc:
            await db.rollback()
            for volume, dicoms in created_paths:
                volume.unlink(missing_ok=True)
                shutil.rmtree(dicoms, ignore_errors=True)
            if isinstance(exc, IntegrityError):
                raise HTTPException(409, "导入与已有检查冲突，请刷新检查列表") from exc
            raise
        return imported[0]
