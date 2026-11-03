from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class PatientBase(BaseModel):
    patient_id: str
    name: str
    gender: Optional[str] = "未知"
    age: Optional[int] = None
    medical_history: Optional[str] = None

class PatientCreate(PatientBase):
    pass

class PatientOut(PatientBase):
    id: int
    doctor_id: int
    created_at: datetime

    class Config:
        from_attributes = True

class StudyTaskOut(BaseModel):
    id: int
    task_code: str
    status: str
    stage_message: Optional[str] = None
    progress: int = 0
    total_execution_time: float = 0.0
    created_at: datetime

    class Config:
        from_attributes = True

class StudyOut(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    study_instance_uid: str
    modality: str
    study_description: Optional[str] = None
    study_date: Optional[datetime] = None
    slice_count: int
    metadata_json: Optional[Dict[str, Any]] = None
    created_at: datetime
    patient: Optional[PatientOut] = None
    tasks: Optional[List[StudyTaskOut]] = None

    class Config:
        from_attributes = True
