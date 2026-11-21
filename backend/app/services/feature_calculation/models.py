from sqlalchemy import BigInteger, String, Integer, SmallInteger, DateTime, JSON, Text
from sqlalchemy.orm import DeclarativeBase, mapped_column

class CalculationBase(DeclarativeBase):
    pass

class CalculationRun(CalculationBase):
    __tablename__ = 'radiomics_calculation_runs'
    id = mapped_column(String(36), primary_key=True)
    analysis_task_id = mapped_column(BigInteger, nullable=False)
    doctor_id = mapped_column(BigInteger, nullable=False)
    created_by_id = mapped_column(BigInteger, nullable=False)
    active_task_id = mapped_column(BigInteger, nullable=True)
    status = mapped_column(String(24), nullable=False)
    config = mapped_column(JSON, nullable=False)
    catalog_version = mapped_column(String(80), nullable=False)
    engine_version = mapped_column(String(80), nullable=False)
    configuration_hash = mapped_column(String(64), nullable=False)
    input_hash = mapped_column(String(64), nullable=False)
    input_snapshot = mapped_column(JSON, nullable=False)
    selected_count = mapped_column(SmallInteger, nullable=False)
    total_lesions = mapped_column(Integer, nullable=False)
    completed_lesions = mapped_column(Integer, nullable=False, default=0)
    error_summary = mapped_column(JSON, nullable=True)
    artifact_manifest = mapped_column(JSON, nullable=True)
    worker_token = mapped_column(String(36), nullable=True)
    heartbeat_at = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, nullable=False)
    started_at = mapped_column(DateTime, nullable=True)
    finished_at = mapped_column(DateTime, nullable=True)

class CalculationResult(CalculationBase):
    __tablename__ = 'radiomics_calculation_results'
    id = mapped_column(String(36), primary_key=True)
    run_id = mapped_column(String(36), nullable=False)
    lesion_id = mapped_column(BigInteger, nullable=False)
    lesion_index = mapped_column(Integer, nullable=False)
    status = mapped_column(String(24), nullable=False)
    selected_count = mapped_column(SmallInteger, nullable=False)
    computed_count = mapped_column(SmallInteger, nullable=False)
    unavailable_count = mapped_column(SmallInteger, nullable=False)
    features = mapped_column(JSON, nullable=False)
    unavailable = mapped_column(JSON, nullable=False)
    provenance = mapped_column(JSON, nullable=False)
    error_message = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, nullable=False)
