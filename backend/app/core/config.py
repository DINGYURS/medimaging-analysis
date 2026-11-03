import os
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

    class Config:
        case_sensitive = True

settings = Settings()
