from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Literal, Self
from pydantic import BaseModel, PositiveInt, field_serializer, model_validator

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
    last_operation_at: Optional[datetime] = None
    patient: Optional[PatientOut] = None
    tasks: Optional[List[StudyTaskOut]] = None

    @field_serializer("last_operation_at", when_used="json-unless-none")
    def serialize_last_operation_at(self, value: datetime) -> str:
        utc_time = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return utc_time.isoformat(timespec="microseconds").replace("+00:00", "Z")

    class Config:
        from_attributes = True


class StudyActivityIn(BaseModel):
    kind: Literal["workspace_opened", "results_viewed"]
    task_id: Optional[PositiveInt] = None

    @model_validator(mode="after")
    def validate_task_id(self) -> Self:
        if (self.kind == "results_viewed") != (self.task_id is not None):
            raise ValueError("查看结果时必须提供任务编号，打开工作台时无需提供")
        return self
