import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, require_doctor
from app.core.config import settings
from app.db.models import AnalysisTask, Study, User
from app.services import ct_chest_trial, ct_reference
from app.services.insight_payload import InputUnavailable
from app.services.insight_service import enqueue_task_insight, latest_insight

router = APIRouter()

async def require_owned_task(task_id: int, db, doctor):
    query = select(AnalysisTask).where(AnalysisTask.id == task_id)
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)
    task = (await db.execute(query)).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "分析任务不存在或无权访问")
    return task

@router.get("/{task_id}")
async def get_insight(task_id: int, db: AsyncSession = Depends(get_db), doctor: User = Depends(require_doctor)):
    task = await require_owned_task(task_id, db, doctor)
    result = await latest_insight(task_id, db)
    trial = await asyncio.to_thread(ct_chest_trial.result_for, task_id, await db.get(Study, task.study_id))
    if trial is not None:
        result["chest_model_trial"] = trial
    return result

@router.post("/{task_id}/generate", status_code=202)
async def generate_insight(task_id: int, db: AsyncSession = Depends(get_db), doctor: User = Depends(require_doctor)):
    task = await require_owned_task(task_id, db, doctor)
    if task.status != "COMPLETED":
        raise HTTPException(409, "请等待影像分析完成")
    if not settings.LLM_WORKER_ENABLED:
        raise HTTPException(503, "AI 洞察尚未完成数据库初始化及启用")
    # Release the read transaction before the enqueue transaction locks this task.
    await db.rollback()
    try:
        await enqueue_task_insight(task_id, force=True)
    except InputUnavailable as exc:
        raise HTTPException(409, str(exc)) from None
    return await latest_insight(task_id, db)


async def ct_reference_source(task, db):
    if task.status != "COMPLETED":
        raise HTTPException(409, "请等待影像分析完成")
    study = await db.get(Study, task.study_id)
    if not study or study.modality != "CT" or not study.nifti_path:
        raise HTTPException(409, "当前任务没有可用的 CT 体数据")
    return ct_reference.ReferenceSource(
        task_id=task.id,
        volume=study.nifti_path,
        dicom_dir=study.dicom_dir,
        series_uid=(study.metadata_json or {}).get("series_instance_uid"),
        lesions=tuple(sorted((x.lesion_index, x.center_x, x.center_y, x.center_z) for x in task.lesions)),
    )


@router.get("/{task_id}/ct-reference")
async def get_ct_reference(task_id: int, db: AsyncSession = Depends(get_db), doctor: User = Depends(require_doctor)):
    task = await require_owned_task(task_id, db, doctor)
    if not settings.CT_REFERENCE_ENABLED:
        return {"status": "NOT_ENABLED"}
    source = await ct_reference_source(task, db)
    return await asyncio.to_thread(ct_reference.status, source)


@router.post("/{task_id}/ct-reference", status_code=202)
async def generate_ct_reference(
    task_id: int, background: BackgroundTasks,
    db: AsyncSession = Depends(get_db), doctor: User = Depends(require_doctor),
):
    task = await require_owned_task(task_id, db, doctor)
    if not settings.CT_REFERENCE_ENABLED:
        raise HTTPException(503, "AI 影像分析参考尚未启用")
    source = await ct_reference_source(task, db)
    await db.rollback()
    lock = ct_reference.reserve(source)
    if lock is not None:
        background.add_task(ct_reference.generate, source, lock)
    return {"status": "RUNNING"}
