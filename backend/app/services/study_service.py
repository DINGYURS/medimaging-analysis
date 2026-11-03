import os
import sys
import shutil
import zipfile
import uuid
from pathlib import Path
from typing import Dict, Any, Tuple
from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Ensure project root is in sys.path so src.* modules can be imported
PROJECT_ROOT = Path("/data/medimaging-analysis")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.dicom_io import load_dicom_series, save_image_to_nifti
from app.core.config import settings
from app.db.models import User, Patient, Study

async def process_and_save_uploaded_dicom(
    file: UploadFile,
    doctor: User,
    db: AsyncSession
) -> Study:
    temp_id = str(uuid.uuid4())[:8]
    temp_extract_dir = Path(settings.STORAGE_DIR) / "temp" / f"upload_{temp_id}"
    temp_extract_dir.mkdir(parents=True, exist_ok=True)
    temp_zip_path = temp_extract_dir / file.filename

    try:
        # 1. Save uploaded file content
        with open(temp_zip_path, "wb") as f:
            while content := await file.read(1024 * 1024):  # 1MB chunks
                f.write(content)

        # 2. Extract if it is a zip archive
        dicom_source_dir = temp_extract_dir / "extracted"
        dicom_source_dir.mkdir(parents=True, exist_ok=True)
        if zipfile.is_zipfile(temp_zip_path):
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                zip_ref.extractall(dicom_source_dir)
        else:
            # Single DICOM file or folder
            shutil.copy(temp_zip_path, dicom_source_dir / file.filename)

        # Search for first directory containing .dcm or any slice
        candidate_dirs = [dicom_source_dir]
        for root, dirs, files in os.walk(dicom_source_dir):
            if any(f.endswith('.dcm') or not '.' in f for f in files):
                candidate_dirs.append(Path(root))
        
        # 3. Parse DICOM series using SimpleITK
        parsed_img = None
        metadata = None
        for cdir in candidate_dirs:
            try:
                parsed_img, metadata = load_dicom_series(cdir)
                if parsed_img and metadata.get("slice_count", 0) > 0:
                    break
            except Exception:
                continue

        if not parsed_img or not metadata:
            raise HTTPException(status_code=400, detail="上传文件中未能解析出有效的 DICOM 影像序列")

        # 4. Check or Create Patient (Strategy A: Isolation per doctor)
        pat_id_str = metadata.get("patient_id") or f"P_{temp_id}"
        query = select(Patient).where(
            Patient.doctor_id == doctor.id,
            Patient.patient_id == pat_id_str
        )
        result = await db.execute(query)
        patient = result.scalar_one_or_none()

        if not patient:
            patient = Patient(
                doctor_id=doctor.id,
                patient_id=pat_id_str,
                name=metadata.get("patient_name") or f"患者_{pat_id_str}",
                gender=metadata.get("patient_sex") or "未知",
                age=metadata.get("patient_age") or None,
                medical_history="常规胸部平扫检查"
            )
            db.add(patient)
            await db.flush()

        # 5. Create Study entry
        study_uid = metadata.get("study_instance_uid") or f"STUDY_{temp_id}_{uuid.uuid4().hex[:6]}"
        study = Study(
            patient_id=patient.id,
            doctor_id=doctor.id,
            study_instance_uid=study_uid,
            modality=metadata.get("modality", "CT"),
            study_description=metadata.get("series_description") or "胸部平扫 CT 检查",
            slice_count=metadata.get("slice_count", 0),
            metadata_json=metadata
        )
        db.add(study)
        await db.flush()

        # 6. Save permanent 3D NIfTI file & DICOM folder
        target_nii_path = Path(settings.NIFTI_DIR) / f"study_{study.id}_volume.nii.gz"
        save_image_to_nifti(parsed_img, target_nii_path)

        target_dicom_dir = Path(settings.DICOM_DIR) / f"study_{study.id}"
        if target_dicom_dir.exists():
            shutil.rmtree(target_dicom_dir)
        shutil.copytree(cdir, target_dicom_dir)

        study.nifti_path = str(target_nii_path)
        study.dicom_dir = str(target_dicom_dir)

        await db.commit()
        await db.refresh(study)
        return study

    finally:
        # Cleanup temporary upload directory
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
