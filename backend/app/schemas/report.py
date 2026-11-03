from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class ReportOut(BaseModel):
    id: int
    task_id: int
    status: str
    llm_model: str
    pre_prompt_payload: Optional[str] = None
    findings: Optional[str] = None
    impression: Optional[str] = None
    recommendations: Optional[str] = None
    doctor_findings: Optional[str] = None
    doctor_impression: Optional[str] = None
    audit_status: str
    doctor_name: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ReportUpdate(BaseModel):
    doctor_findings: Optional[str] = None
    doctor_impression: Optional[str] = None
    doctor_name: Optional[str] = None
    audit_status: Optional[str] = "DOCTOR_CONFIRMED"
