from typing import Any
import os
import sys
import time
import uuid
import json
import asyncio
from pathlib import Path
from datetime import datetime
from sqlalchemy import select

PROJECT_ROOT = Path("/data/medimaging-analysis")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import SimpleITK as sitk
import numpy as np
from src.dicom_io import load_dicom_series
from src.lesion_detector import run_nnunet_inference
from src.radiomics_engine import (
    extract_lesion_radiomics,
    get_default_radiomics_extractor,
    get_configurable_radiomics_extractor
)
from app.services.liver_mri_detector import analysis_route, detect_liver_mri
from app.services.insight_service import enqueue_safely
from app.services.insight_payload import source_record

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.db.session import AsyncSessionLocal
from app.db.models import AnalysisTask, Study, Lesion, RadiomicsFeature

def _execute_sync_pipeline(nii_volume_path: str, mask_output_path: str, cuda_device: str = "2"):
    """Synchronous CPU/GPU heavy pipeline executed in thread pool."""
    image_sitk = sitk.ReadImage(nii_volume_path)
    
    # Never substitute a fixed sample mask for this patient's inference.
    inference_dir = Path(mask_output_path).parent / (Path(mask_output_path).stem + "_inference")
    mask_path, lesion_findings = run_nnunet_inference(
        input_nii_path=nii_volume_path,
        output_dir=inference_dir,
        cuda_device=cuda_device,
        min_volume_mm3=25.0
    )

    # Ensure mask is saved to target path
    if str(mask_path) != mask_output_path:
        mask_sitk = sitk.ReadImage(str(mask_path))
        sitk.WriteImage(mask_sitk, mask_output_path, True)
    else:
        mask_sitk = sitk.ReadImage(mask_output_path)

    # 2. PyRadiomics 107-dim feature extraction
    extractor = get_default_radiomics_extractor(bin_width=25.0)
    if mask_sitk.GetSize() != image_sitk.GetSize():
        resampler = sitk.ResampleImageFilter()
        resampler.SetReferenceImage(image_sitk)
        resampler.SetInterpolator(sitk.sitkNearestNeighbor)
        mask_sitk = resampler.Execute(mask_sitk)

    from scipy import ndimage as ndi
    mask_arr = sitk.GetArrayFromImage(mask_sitk)
    labeled_mask, _ = ndi.label(mask_arr > 0)

    radiomics_results = []
    for item in lesion_findings:
        lid = item["lesion_id"]
        lbl_idx = item["label_index"]
        single_lesion_arr = (labeled_mask == lbl_idx).astype(np.uint8)
        single_mask_img = sitk.GetImageFromArray(single_lesion_arr)
        single_mask_img.CopyInformation(image_sitk)

        radiomics_data = extract_lesion_radiomics(
            image=image_sitk,
            mask=single_mask_img,
            lesion_id=lid,
            extractor=extractor
        )
        radiomics_results.append(radiomics_data)

    Path(mask_output_path + ".source.json").write_text(
        json.dumps(source_record(nii_volume_path, mask_output_path)), encoding="utf-8")
    return lesion_findings, radiomics_results

async def run_task_pipeline_background(task_id: int):
    start_time = time.time()
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(AnalysisTask).where(AnalysisTask.id == task_id))
        task = res.scalar_one_or_none()
        if not task:
            return

        study_res = await db.execute(select(Study).where(Study.id == task.study_id))
        study = study_res.scalar_one_or_none()
        if not study:
            task.status = "FAILED"
            task.error_message = "未找到关联的检查数据"
            await db.commit()
            return

        try:
            metadata = study.metadata_json or {}
            is_mri = analysis_route(study.modality, metadata) == 'liver_mri'
            # 1. Acquire GPU Gate (Queue limit)
            task.status = "QUEUED"
            task.stage_message = "正在等待 GPU 推理资源分配..."
            await db.commit()

            await gpu_gate.acquire()
            try:
                task.status = "SEGMENTING"
                task.progress = 30
                task.stage_message = "正在识别肝脏 MRI 病灶…" if is_mri else "正在调用 nnU-Net 3D 模型推理肺部病灶..."
                await db.commit()

                mask_file = Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz"
                
                # Execute in thread to keep async event loop responsive
                if is_mri:
                    lesion_findings = await asyncio.to_thread(
                        detect_liver_mri, study.nifti_path, str(mask_file), metadata,
                    )
                    radiomics_results = []
                else:
                    lesion_findings, radiomics_results = await asyncio.to_thread(
                        _execute_sync_pipeline,
                        nii_volume_path=study.nifti_path,
                        mask_output_path=str(mask_file),
                        cuda_device="2",
                    )
            finally:
                # Release GPU lock as soon as segmentation finishes!
                gpu_gate.release()

            # 2. Extracting & Saving Features
            task.status = "EXTRACTING"
            task.progress = 75
            task.stage_message = "正在保存肝脏 MRI 病灶掩膜…" if is_mri else "正在保存影像组学定量分析结果..."
            await db.commit()

            # Save full radiomics JSON
            rad_json_file = Path(settings.RADIOMICS_DIR) / f"task_{task.id}_features.json"
            if not is_mri:
                temp_json_file = rad_json_file.with_suffix("." + uuid.uuid4().hex + ".tmp")
                temp_json_file.write_text(json.dumps(radiomics_results, ensure_ascii=False, indent=2), encoding="utf-8")
                temp_json_file.replace(rad_json_file)

            # Insert Lesion and RadiomicsFeature rows into MySQL
            for idx, item in enumerate(lesion_findings):
                rad_data = radiomics_results[idx] if idx < len(radiomics_results) else {}
                summary = rad_data.get("clinical_summary", {})
                
                lesion = Lesion(
                    task_id=task.id,
                    lesion_index=item["lesion_id"],
                    volume_mm3=item["volume_mm3"],
                    equivalent_diameter_mm=item["equivalent_diameter_mm"],
                    center_x=item["centroid_world_lps_mm"][0],
                    center_y=item["centroid_world_lps_mm"][1],
                    center_z=item["centroid_world_lps_mm"][2],
                    mask_nifti_path=str(mask_file),
                    density_type="MRI 病灶" if is_mri else summary.get("attenuation_category", "纯磨玻璃结节 (pGGO)")
                )
                db.add(lesion)
                await db.flush()

                if is_mri:
                    continue
                rad_feature = RadiomicsFeature(
                    lesion_id=lesion.id,
                    feature_count=rad_data.get("total_extracted_features_count", 107),
                    shape_features=rad_data.get("features_by_class", {}).get("shape", {}),
                    firstorder_features=rad_data.get("features_by_class", {}).get("firstorder", {}),
                    texture_features=rad_data.get("features_by_class", {}).get("texture", {}),
                    full_features_json_path=str(rad_json_file)
                )
                db.add(rad_feature)

            # 4. Mark Task Completed
            task.status = "COMPLETED"
            task.progress = 100
            if is_mri:
                task.stage_message = "肝脏 MRI 分割完成，可进入特征计算" if lesion_findings else "肝脏 MRI 分割完成，模型未检出病灶"
            else:
                task.stage_message = "全流程智能分析完成"
            task.total_execution_time = round(time.time() - start_time, 2)
            task.finished_at = datetime.utcnow()
            await db.commit()
            if not is_mri:
                await enqueue_safely(task_id)

        except Exception as e:
            import traceback
            task.status = "FAILED"
            task.error_message = f"流水线执行异常: {str(e)}\n{traceback.format_exc()[:500]}"
            task.total_execution_time = round(time.time() - start_time, 2)
            await db.commit()


async def recalculate_task_radiomics(
    task_id: int,
    config: Any
) -> list[dict[str, Any]]:
    """
    ????? IBSI ?????????????????????
    ???????? JSON ????????????.
    """
    from sqlalchemy.orm import selectinload

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(AnalysisTask)
            .options(
                selectinload(AnalysisTask.study),
                selectinload(AnalysisTask.lesions).selectinload(Lesion.radiomics)
            )
            .where(AnalysisTask.id == task_id)
        )
        task = res.scalar_one_or_none()
        if not task or not task.lesions:
            raise ValueError("????????????")

        study = task.study
        if not study or not study.nifti_path or not os.path.exists(study.nifti_path):
            raise ValueError("??????????")

        mask_file = task.lesions[0].mask_nifti_path
        if not mask_file or not os.path.exists(mask_file):
            raise ValueError("???????????")

        resampled_spacing = None
        if config.resampling_mode == "isotropic_1mm":
            resampled_spacing = (1.0, 1.0, 1.0)
        elif config.resampling_mode == "custom" and config.custom_spacing:
            resampled_spacing = tuple(config.custom_spacing)

        resegment_range = None
        if config.resegmentation_enabled:
            resegment_range = (config.resegment_min_hu, config.resegment_max_hu)

        config_summary = {
            "resampling_mode": config.resampling_mode,
            "custom_spacing": list(resampled_spacing) if resampled_spacing else "native",
            "bin_width": config.bin_width,
            "interpolator": config.interpolator,
            "resegmentation_enabled": config.resegmentation_enabled,
            "resegment_min_hu": config.resegment_min_hu,
            "resegment_max_hu": config.resegment_max_hu,
            "selected_classes": config.selected_classes
        }

        def _sync_recalculate():
            image_sitk = sitk.ReadImage(study.nifti_path)
            mask_sitk = sitk.ReadImage(mask_file)

            if mask_sitk.GetSize() != image_sitk.GetSize():
                resampler = sitk.ResampleImageFilter()
                resampler.SetReferenceImage(image_sitk)
                resampler.SetInterpolator(sitk.sitkNearestNeighbor)
                mask_sitk = resampler.Execute(mask_sitk)

            from scipy import ndimage as ndi
            mask_arr = sitk.GetArrayFromImage(mask_sitk)
            labeled_mask, num_labels = ndi.label(mask_arr > 0)

            extractor = get_configurable_radiomics_extractor(
                bin_width=config.bin_width,
                resampled_spacing=resampled_spacing,
                interpolator=config.interpolator,
                feature_classes=config.selected_classes,
                resegment_range=resegment_range
            )

            new_results = []
            for lesion in task.lesions:
                lid = lesion.lesion_index
                single_lesion_arr = (labeled_mask == lid).astype(np.uint8)
                if np.sum(single_lesion_arr) == 0:
                    single_lesion_arr = (mask_arr > 0).astype(np.uint8)

                single_mask_img = sitk.GetImageFromArray(single_lesion_arr)
                single_mask_img.CopyInformation(image_sitk)

                radiomics_data = extract_lesion_radiomics(
                    image=image_sitk,
                    mask=single_mask_img,
                    lesion_id=lid,
                    extractor=extractor,
                    config_summary=config_summary
                )
                new_results.append(radiomics_data)

            return new_results

        new_results = await asyncio.to_thread(_sync_recalculate)

        # Update JSON file
        rad_json_file = Path(settings.RADIOMICS_DIR) / f"task_{task.id}_features.json"
        temp_json_file = rad_json_file.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temp_json_file.write_text(json.dumps(new_results, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_json_file.replace(rad_json_file)

        # Update DB records
        for idx, lesion in enumerate(task.lesions):
            if idx < len(new_results):
                rad_data = new_results[idx]
                rad_rec = lesion.radiomics
                if rad_rec:
                    rad_rec.feature_count = rad_data.get("total_extracted_features_count", len(rad_data.get("all_raw_features", {})))
                    rad_rec.shape_features = rad_data.get("features_by_class", {}).get("shape", {})
                    rad_rec.firstorder_features = rad_data.get("features_by_class", {}).get("firstorder", {})
                    rad_rec.texture_features = rad_data.get("features_by_class", {}).get("texture", {})
                    rad_rec.full_features_json_path = str(rad_json_file)
                sum_data = rad_data.get("clinical_summary", {})
                if sum_data.get("attenuation_category"):
                    lesion.density_type = sum_data["attenuation_category"]

        await db.commit()
        await enqueue_safely(task_id)
        return new_results
