import os
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_doctor
from app.db.models import User, AnalysisTask, Lesion, RadiomicsFeature, ClinicalReport
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
                    features_dict = json.load(f)
            except Exception:
                pass

        lesions_data.append({
            "id": lesion.id,
            "lesion_index": lesion.lesion_index,
            "volume_mm3": lesion.volume_mm3,
            "equivalent_diameter_mm": lesion.equivalent_diameter_mm,
            "center_coord": [lesion.center_x, lesion.center_y, lesion.center_z],
            "density_type": lesion.density_type,
            "features": features_dict,
            "radiomics_summary": {
                "shape": rad.shape_features if rad else {},
                "firstorder": rad.firstorder_features if rad else {},
                "texture": rad.texture_features if rad else {}
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
        raise HTTPException(status_code=404, detail="组学特征文件未找到")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data
