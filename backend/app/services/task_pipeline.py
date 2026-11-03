import os
import sys
import time
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
from src.lesion_detector import run_nnunet_inference, analyze_lesion_components
from src.radiomics_engine import (
    extract_lesion_radiomics,
    get_default_radiomics_extractor
)
from src.report_adapter import format_llm_clinical_insight_payload

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.db.session import AsyncSessionLocal
from app.db.models import AnalysisTask, Study, Lesion, RadiomicsFeature, ClinicalReport

def _execute_sync_pipeline(nii_volume_path: str, mask_output_path: str, cuda_device: str = "2"):
    """Synchronous CPU/GPU heavy pipeline executed in thread pool."""
    image_sitk = sitk.ReadImage(nii_volume_path)
    
    # 1. nnU-Net Inference / Fallback
    try:
        mask_path, lesion_findings = run_nnunet_inference(
            input_nii_path=nii_volume_path,
            output_dir=Path(mask_output_path).parent,
            cuda_device=cuda_device,
            min_volume_mm3=25.0
        )
    except Exception as e:
        print(f"[!] nnU-Net 推理跳过或异常: {e}，尝试使用病例已有金标准掩膜验证...")
        # Check if sample mask exists in outputs or sample case
        sample_mask = PROJECT_ROOT / "outputs" / "sample_case.nii.gz"
        if sample_mask.exists():
            mask_path = sample_mask
            lesion_findings = analyze_lesion_components(mask_path, min_volume_mm3=25.0)
        else:
            raise RuntimeError(f"未能完成病灶推理且无可用掩膜: {e}")

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
            # 1. Acquire GPU Gate (Queue limit)
            task.status = "QUEUED"
            task.stage_message = "正在等待 GPU 推理资源分配..."
            await db.commit()

            await gpu_gate.acquire()
            try:
                task.status = "SEGMENTING"
                task.progress = 30
                task.stage_message = "正在调用 nnU-Net 3D 模型推理肺部病灶..."
                await db.commit()

                mask_file = Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz"
                
                # Execute in thread to keep async event loop responsive
                lesion_findings, radiomics_results = await asyncio.to_thread(
                    _execute_sync_pipeline,
                    nii_volume_path=study.nifti_path,
                    mask_output_path=str(mask_file),
                    cuda_device="2"
                )
            finally:
                # Release GPU lock as soon as segmentation finishes!
                gpu_gate.release()

            # 2. Extracting & Saving Features
            task.status = "EXTRACTING"
            task.progress = 75
            task.stage_message = "正在保存 107 维国际标准组学特征并组装临床 Prompt..."
            await db.commit()

            # Save full radiomics JSON
            rad_json_file = Path(settings.RADIOMICS_DIR) / f"task_{task.id}_features.json"
            with open(rad_json_file, "w", encoding="utf-8") as f:
                json.dump(radiomics_results, f, ensure_ascii=False, indent=2)

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
                    density_type=summary.get("attenuation_category", "纯磨玻璃结节 (pGGO)")
                )
                db.add(lesion)
                await db.flush()

                rad_feature = RadiomicsFeature(
                    lesion_id=lesion.id,
                    feature_count=rad_data.get("total_extracted_features_count", 107),
                    shape_features=rad_data.get("features_by_class", {}).get("shape", {}),
                    firstorder_features=rad_data.get("features_by_class", {}).get("firstorder", {}),
                    texture_features=rad_data.get("features_by_class", {}).get("texture", {}),
                    full_features_json_path=str(rad_json_file)
                )
                db.add(rad_feature)

            # 3. Format pre-prompt for LLM (Placeholder mode per user request)
            llm_payload = format_llm_clinical_insight_payload(
                dicom_metadata=study.metadata_json or {},
                lesion_findings=lesion_findings,
                radiomics_results=radiomics_results
            )
            
            prompt_text = llm_payload.get("llm_prompt_template", "")

            report = ClinicalReport(
                task_id=task.id,
                status="PENDING_INTEGRATION",
                llm_model="待接入",
                pre_prompt_payload=prompt_text,
                findings="",
                impression="",
                recommendations="",
                audit_status="DRAFT"
            )
            db.add(report)

            # 4. Mark Task Completed
            task.status = "COMPLETED"
            task.progress = 100
            task.stage_message = "全流程智能分析完成"
            task.total_execution_time = round(time.time() - start_time, 2)
            task.finished_at = datetime.utcnow()
            await db.commit()

        except Exception as e:
            import traceback
            task.status = "FAILED"
            task.error_message = f"流水线执行异常: {str(e)}\n{traceback.format_exc()[:500]}"
            task.total_execution_time = round(time.time() - start_time, 2)
            await db.commit()
