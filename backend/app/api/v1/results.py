import sys
from pathlib import Path
PROJECT_ROOT = Path('/data/medimaging-analysis')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import os
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from fastapi.responses import Response, FileResponse
from app.schemas.radiomics import (
    RadiomicsCatalogResponse,
    RadiomicsCalculationConfig,
    RadiomicsExportRequest
)
from src.radiomics_engine import (
    get_radiomics_catalog_metadata,
    export_radiomics_to_excel,
    export_radiomics_to_csv
)
from app.services.task_pipeline import recalculate_task_radiomics

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_doctor
from app.db.models import User, AnalysisTask, Lesion, RadiomicsFeature, ClinicalReport, Study
from app.api.v1.studies import stream_file_with_range

router = APIRouter()

@router.get("/{task_id}")
@router.get("/{task_id}/results")
async def get_task_analysis_results(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """获取任务检出的病灶列表、临床报告及组学核心量化指标."""
    query = (
        select(AnalysisTask)
        .options(
            selectinload(AnalysisTask.lesions).selectinload(Lesion.radiomics),
            selectinload(AnalysisTask.reports)
        )
        .where(AnalysisTask.id == task_id)
    )
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)

    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="分析任务不存在或无权访问")

    lesions_data = []
    for lesion in task.lesions:
        rad = lesion.radiomics
        features_dict = {}
        if rad and rad.full_features_json_path and os.path.exists(rad.full_features_json_path):
            try:
                with open(rad.full_features_json_path, "r", encoding="utf-8") as f:
                    full_json = json.load(f)
                    if isinstance(full_json, list):
                        for item in full_json:
                            if item.get("lesion_id") == lesion.lesion_index:
                                features_dict = item
                                break
                        if not features_dict and full_json:
                            features_dict = full_json[0]
                    else:
                        features_dict = full_json
            except Exception:
                pass

        by_class = features_dict.get("features_by_class", {})
        shape_data = rad.shape_features if (rad and rad.shape_features) else by_class.get("shape", features_dict.get("morphology", {}))
        firstorder_data = rad.firstorder_features if (rad and rad.firstorder_features) else by_class.get("firstorder", features_dict.get("intensity", {}))
        texture_data = rad.texture_features if (rad and rad.texture_features) else by_class.get("texture", features_dict.get("texture", {}))

        lesions_data.append({
            "id": lesion.id,
            "lesion_index": lesion.lesion_index,
            "volume_mm3": lesion.volume_mm3,
            "equivalent_diameter_mm": lesion.equivalent_diameter_mm,
            "center_coord": [lesion.center_x, lesion.center_y, lesion.center_z],
            "density_type": lesion.density_type,
            "features": features_dict,
            "radiomics_summary": {
                "shape": shape_data or {},
                "firstorder": firstorder_data or {},
                "texture": texture_data or {}
            }
        })

    report_data = None
    if task.reports:
        rep = task.reports[0]
        report_data = {
            "id": rep.id,
            "task_id": rep.task_id,
            "status": rep.status,
            "llm_model": rep.llm_model,
            "pre_prompt_payload": rep.pre_prompt_payload,
            "findings": rep.findings,
            "impression": rep.impression,
            "doctor_findings": rep.doctor_findings or "",
            "doctor_impression": rep.doctor_impression or "",
            "audit_status": rep.audit_status,
            "doctor_name": rep.doctor_name,
            "confirmed_at": rep.confirmed_at.isoformat() if rep.confirmed_at else None
        }

    return {
        "task_id": task.id,
        "task_code": task.task_code,
        "status": task.status,
        "total_lesions_count": len(lesions_data),
        "lesions": lesions_data,
        "report": report_data
    }

@router.get("/{task_id}/mask")
async def stream_task_mask(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """流式获取 3D 病灶 Mask NIfTI 文件."""
    query = (
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.lesions))
        .where(AnalysisTask.id == task_id)
    )
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)

    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task or not task.lesions:
        raise HTTPException(status_code=404, detail="分析任务或病灶掩膜不存在")

    mask_path = task.lesions[0].mask_nifti_path
    if not mask_path or not os.path.exists(mask_path):
        raise HTTPException(status_code=404, detail="掩膜物理文件未找到")

    return stream_file_with_range(request, mask_path, media_type="application/gzip")

@router.get("/{task_id}/radiomics-json")
async def get_full_radiomics_json(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """下载或查看 107 维 IBSI 国际标准全量组学指标 JSON."""
    query = (
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.lesions).selectinload(Lesion.radiomics))
        .where(AnalysisTask.id == task_id)
    )
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)

    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task or not task.lesions:
        raise HTTPException(status_code=404, detail="未找到组学特征数据")

    json_path = task.lesions[0].radiomics.full_features_json_path if task.lesions[0].radiomics else None
    if not json_path or not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="未找到全量组学特征数据，请先重新计算特征")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


@router.get("/{task_id}/radiomics-catalog")
async def get_task_radiomics_catalog(
    doctor: User = Depends(require_doctor)
):
    """获取 IBSI 规范的放射组学特征目录与预设参数."""
    return get_radiomics_catalog_metadata()


@router.post("/{task_id}/radiomics/recalculate")
async def recalculate_task_features(
    task_id: int,
    config: RadiomicsCalculationConfig,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """根据医生自定义的 IBSI 参数实时重新提取与计算特征."""
    query = select(AnalysisTask).where(AnalysisTask.id == task_id)
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)
    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="分析任务不存在或未检出有效病灶")

    modality = await db.scalar(select(Study.modality).where(Study.id == task.study_id))
    if modality in {"MR", "MRI"}:
        raise HTTPException(422, "MRI 请使用特征计算页面选择 3D 特征")
    try:
        await recalculate_task_radiomics(task_id, config)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"放射组学特征重算失败: {str(e)}")

    return await get_task_analysis_results(task_id, db=db, doctor=doctor)


@router.post("/{task_id}/radiomics/export")
async def export_task_radiomics(
    task_id: int,
    req: RadiomicsExportRequest,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """导出 IBSI 规范的组学生物标志物矩阵文件 (Excel / CSV)."""
    query = (
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.lesions).selectinload(Lesion.radiomics))
        .where(AnalysisTask.id == task_id)
    )
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)
    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task or not task.lesions:
        raise HTTPException(status_code=404, detail="未找到全量组学特征数据，请先重新计算特征")

    json_path = task.lesions[0].radiomics.full_features_json_path if task.lesions[0].radiomics else None
    if not json_path or not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="未找到全量组学特征数据，请先重新计算特征")

    with open(json_path, "r", encoding="utf-8") as f:
        radiomics_results = json.load(f)

    config_summary = {
        "resampling_mode": req.resampling_mode,
        "custom_spacing": req.custom_spacing,
        "bin_width": req.bin_width,
        "interpolator": req.interpolator,
        "resegmentation_enabled": req.resegmentation_enabled,
        "resegment_min_hu": req.resegment_min_hu,
        "resegment_max_hu": req.resegment_max_hu,
        "selected_classes": req.selected_classes
    }

    if req.format.lower() == "excel":
        excel_bytes = export_radiomics_to_excel(
            radiomics_results,
            config_summary=config_summary,
            selected_keys=req.selected_keys
        )
        filename = f"radiomics_task_{task.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    else:
        csv_str = export_radiomics_to_csv(radiomics_results, selected_keys=req.selected_keys)
        filename = f"radiomics_task_{task.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            content=csv_str.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
