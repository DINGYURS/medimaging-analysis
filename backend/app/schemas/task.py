from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class TaskCreate(BaseModel):
    study_id: int

class TaskStatusOut(BaseModel):
    task_id: int
    task_code: str
    status: str
    queue_position: int
    progress: int
    stage_message: str
    error_message: Optional[str] = None
    total_execution_time: float
    created_at: datetime
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class LesionOut(BaseModel):
    id: int
    lesion_index: int
    volume_mm3: float
    equivalent_diameter_mm: float
    center_x: float
    center_y: float
    center_z: float
    density_type: str
    created_at: datetime
    radiomics: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
