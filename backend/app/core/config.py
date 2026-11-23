import os
from pathlib import Path
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "MedImaging Analysis Platform"
    API_V1_STR: str = "/api/v1"
    
    SECRET_KEY: str = os.getenv("SECRET_KEY", "medimaging-super-secret-jwt-key-2026-medical-ai")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    
    MYSQL_USER: str = os.getenv("MYSQL_USER", "medimage")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "medimage123456")
    MYSQL_SERVER: str = os.getenv("MYSQL_SERVER", "127.0.0.1")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_DB: str = os.getenv("MYSQL_DB", "medimaging-analysis")
    
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return f"mysql+asyncmy://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset=utf8mb4"
    
    # 候选病灶检测模型服务端点 (对接服务器常驻运行的 Dataset712 模型服务集群)
    LUNG_NODULE_CANDIDATE_ENDPOINT: str = os.getenv(
        "LUNG_NODULE_CANDIDATE_ENDPOINT",
        "http://127.0.0.1:8112,http://127.0.0.1:8113,http://127.0.0.1:8114,http://127.0.0.1:8115"
    )

    LIVER_MRI_ENDPOINT: str = "http://127.0.0.1:8123"
    LIVER_MRI_RUN_ROOT: str = "/data/medicine_image_mask/liver_mri_detector/api_runs/liver_mri"
    LIVER_MRI_CHECKPOINT_SHA256: str = "b42c809e66011cc48c4869d79c3d40bace6867792b166d323b1ace3ea01ff152"

    # GPU 并发限流配置 (用户要求默认最多 2 个并行推理，支持动态调整)
    MAX_GPU_CONCURRENT_TASKS: int = int(os.getenv("MAX_GPU_CONCURRENT_TASKS", "2"))
    
    # 存储目录
    BASE_DIR: str = "/data/medimaging-analysis"
    STORAGE_DIR: str = os.path.join(BASE_DIR, "storage")
    DICOM_DIR: str = os.path.join(STORAGE_DIR, "dicom")
    NIFTI_DIR: str = os.path.join(STORAGE_DIR, "nifti")
    MASK_DIR: str = os.path.join(STORAGE_DIR, "masks")
    RADIOMICS_DIR: str = os.path.join(STORAGE_DIR, "radiomics")
    REPORTS_DIR: str = os.path.join(STORAGE_DIR, "reports")

    CT_QUANTITATIVE_INSIGHT_ENABLED: bool = False
    CT_QUANTITATIVE_MASK_DIR: str = ""
    CT_REFERENCE_ENABLED: bool = False
    CT_REFERENCE_LOBE_PYTHON: str = ""
    CT_REFERENCE_LOBE_WEIGHTS: str = ""
    CT_REFERENCE_LOBE_GPU: int = Field(default=3, ge=0)
    CT_REFERENCE_TEXTURE_PYTHON: str = ""
    CT_REFERENCE_TEXTURE_WEIGHTS: str = ""

    # No schema initialization or cloud job writes until migration approval.
    INITIALIZE_DATABASE_ON_STARTUP: bool = False
    LLM_WORKER_ENABLED: bool = False
    LLM_API_BASE_URL: str = "https://token.aigcit.com/v1"
    LLM_MODEL: str = "deepseek-v4-flash"
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_JSON_MODE: bool = True
    LLM_TIMEOUT_SECONDS: float = Field(default=90, ge=5, le=300)
    LLM_MAX_LESIONS: int = Field(default=50, ge=1, le=100)
    LLM_MAX_INPUT_BYTES: int = Field(default=100000, ge=4096, le=500000)
    LLM_MAX_TOKENS: int = Field(default=4096, ge=512, le=16384)
    LLM_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=3)
    LLM_POLL_INTERVAL_SECONDS: float = Field(default=3, ge=1, le=60)

    class Config:
        case_sensitive = True
        env_file = str(Path(__file__).resolve().parents[2] / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
