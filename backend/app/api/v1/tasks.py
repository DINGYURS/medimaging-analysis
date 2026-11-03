import uuid
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_db, require_doctor
from app.db.models import User, Study, AnalysisTask
from app.schemas.task import TaskCreate, TaskStatusOut
from app.services.task_pipeline import run_task_pipeline_background
from app.core.queue_gate import gpu_gate

router = APIRouter()

@router.post("/analyze", response_model=TaskStatusOut)
async def submit_analysis_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """发起端到端智能分析流水线 (受 GPU 并发门控限制，多任务安全排队)."""
    # 1. Check study ownership
    query = select(Study).where(Study.id == payload.study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)
    res = await db.execute(query)
    study = res.scalar_one_or_none()
    if not study:
        raise HTTPException(status_code=404, detail="检查记录不存在或无权访问")

    # 2. Calculate current queue position
    q_count_res = await db.execute(
        select(func.count(AnalysisTask.id)).where(AnalysisTask.status.in_(["QUEUED", "SEGMENTING"]))
    )
    current_waiting = q_count_res.scalar() or 0

    task_code = f"TASK_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6].upper()}"
    task = AnalysisTask(
        study_id=study.id,
        doctor_id=doctor.id,
        task_code=task_code,
        status="QUEUED",
        queue_position=current_waiting,
        progress=0,
        stage_message=f"任务已加入排队队列 (当前前方等待任务: {current_waiting})"
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 3. Fire-and-forget async task worker (managed by asyncio and GPU gate)
    asyncio.create_task(run_task_pipeline_background(task.id))

    return {
        "task_id": task.id,
        "task_code": task.task_code,
        "status": task.status,
        "queue_position": task.queue_position,
        "progress": task.progress,
        "stage_message": task.stage_message,
        "error_message": task.error_message,
        "total_execution_time": task.total_execution_time,
        "created_at": task.created_at,
        "finished_at": task.finished_at
    }

@router.get("/{task_id}/status", response_model=TaskStatusOut)
async def get_task_status(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """轮询任务当前执行阶段与百分比."""
    query = select(AnalysisTask).where(AnalysisTask.id == task_id)
    if doctor.role != "ADMIN":
        query = query.where(AnalysisTask.doctor_id == doctor.id)

    res = await db.execute(query)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="分析任务不存在或无权访问")

    return {
        "task_id": task.id,
        "task_code": task.task_code,
        "status": task.status,
        "queue_position": task.queue_position,
        "progress": task.progress,
        "stage_message": task.stage_message,
        "error_message": task.error_message,
        "total_execution_time": task.total_execution_time,
        "created_at": task.created_at,
        "finished_at": task.finished_at
    }

@router.get("/queue/stats")
async def get_queue_statistics(doctor: User = Depends(require_doctor)):
    """获取当前 GPU 任务排队门控实时状态."""
    return gpu_gate.get_stats()
