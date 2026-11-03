from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, require_doctor
from app.db.models import User, AnalysisTask, ClinicalReport
from app.schemas.report import ReportOut, ReportUpdate

router = APIRouter()

@router.get("/{task_id}", response_model=ReportOut)
async def get_clinical_report(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """读取临床报告详情 (包含预组装好的 Prompt 报文与待接入状态)."""
    # Verify task ownership
    task_query = select(AnalysisTask).where(AnalysisTask.id == task_id)
    if doctor.role != "ADMIN":
        task_query = task_query.where(AnalysisTask.doctor_id == doctor.id)
    task_res = await db.execute(task_query)
    task = task_res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="关联任务不存在或无权访问")

    report_res = await db.execute(select(ClinicalReport).where(ClinicalReport.task_id == task_id))
    report = report_res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="该任务尚未生成报告记录")

    return report

@router.put("/{report_id}", response_model=ReportOut)
async def update_doctor_report(
    report_id: int,
    payload: ReportUpdate,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """医生在前端修订影像所见与诊断意见，签署确认."""
    report_res = await db.execute(select(ClinicalReport).where(ClinicalReport.id == report_id))
    report = report_res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    if payload.doctor_findings is not None:
        report.doctor_findings = payload.doctor_findings
    if payload.doctor_impression is not None:
        report.doctor_impression = payload.doctor_impression
    if payload.doctor_name is not None:
        report.doctor_name = payload.doctor_name
    else:
        report.doctor_name = doctor.real_name

    if payload.audit_status is not None:
        report.audit_status = payload.audit_status

    report.confirmed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(report)
    return report
