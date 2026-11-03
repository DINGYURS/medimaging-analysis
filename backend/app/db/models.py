from datetime import datetime
from sqlalchemy import Column, BigInteger, String, Integer, Float, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    real_name = Column(String(64), nullable=False)
    role = Column(String(20), nullable=False, default="DOCTOR", index=True)  # ADMIN / DOCTOR
    department = Column(String(64), nullable=True, default="放射科")
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    patients = relationship("Patient", back_populates="doctor", cascade="all, delete-orphan")
    studies = relationship("Study", back_populates="doctor", cascade="all, delete-orphan")
    tasks = relationship("AnalysisTask", back_populates="doctor", cascade="all, delete-orphan")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    doctor_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(String(64), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    gender = Column(String(10), nullable=True, default="未知")
    age = Column(Integer, nullable=True)
    medical_history = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    doctor = relationship("User", back_populates="patients")
    studies = relationship("Study", back_populates="patient", cascade="all, delete-orphan")


class Study(Base):
    __tablename__ = "studies"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    patient_id = Column(BigInteger, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    study_instance_uid = Column(String(128), unique=True, nullable=False, index=True)
    modality = Column(String(20), default="CT", nullable=False)
    study_description = Column(String(255), nullable=True)
    study_date = Column(DateTime, nullable=True)
    slice_count = Column(Integer, default=0, nullable=False)
    dicom_dir = Column(String(512), nullable=True)
    nifti_path = Column(String(512), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    patient = relationship("Patient", back_populates="studies", lazy="selectin")
    doctor = relationship("User", back_populates="studies")
    tasks = relationship("AnalysisTask", back_populates="study", cascade="all, delete-orphan", lazy="selectin")


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    study_id = Column(BigInteger, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_code = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(String(32), default="QUEUED", nullable=False, index=True)  # QUEUED, SEGMENTING, EXTRACTING, COMPLETED, FAILED
    queue_position = Column(Integer, default=0, nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    stage_message = Column(String(255), default="任务已加入排队队列", nullable=False)
    error_message = Column(Text, nullable=True)
    total_execution_time = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    study = relationship("Study", back_populates="tasks")
    doctor = relationship("User", back_populates="tasks")
    lesions = relationship("Lesion", back_populates="task", cascade="all, delete-orphan", lazy="selectin")
    reports = relationship("ClinicalReport", back_populates="task", cascade="all, delete-orphan", lazy="selectin")


class Lesion(Base):
    __tablename__ = "lesions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    lesion_index = Column(Integer, default=1, nullable=False)
    volume_mm3 = Column(Float, default=0.0, nullable=False)
    equivalent_diameter_mm = Column(Float, default=0.0, nullable=False)
    center_x = Column(Float, default=0.0, nullable=False)
    center_y = Column(Float, default=0.0, nullable=False)
    center_z = Column(Float, default=0.0, nullable=False)
    mask_nifti_path = Column(String(512), nullable=True)
    density_type = Column(String(64), default="纯磨玻璃结节 (pGGO)", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("AnalysisTask", back_populates="lesions")
    radiomics = relationship("RadiomicsFeature", back_populates="lesion", uselist=False, cascade="all, delete-orphan", lazy="selectin")


class RadiomicsFeature(Base):
    __tablename__ = "radiomics_features"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    lesion_id = Column(BigInteger, ForeignKey("lesions.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_count = Column(Integer, default=107, nullable=False)
    shape_features = Column(JSON, nullable=True)
    firstorder_features = Column(JSON, nullable=True)
    texture_features = Column(JSON, nullable=True)
    full_features_json_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lesion = relationship("Lesion", back_populates="radiomics")


class ClinicalReport(Base):
    __tablename__ = "clinical_reports"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), default="PENDING_INTEGRATION", nullable=False)  # PENDING_INTEGRATION, DRAFT, CONFIRMED
    llm_model = Column(String(64), default="大模型待接入", nullable=False)
    pre_prompt_payload = Column(Text, nullable=True)
    findings = Column(Text, nullable=True)
    impression = Column(Text, nullable=True)
    recommendations = Column(Text, nullable=True)
    doctor_findings = Column(Text, nullable=True)
    doctor_impression = Column(Text, nullable=True)
    audit_status = Column(String(32), default="DRAFT", nullable=False)  # DRAFT, DOCTOR_CONFIRMED, ARCHIVED
    doctor_name = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("AnalysisTask", back_populates="reports")
