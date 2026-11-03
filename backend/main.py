import sys
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure paths
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.init_db import init_database
from app.api.v1 import auth, admin, studies, tasks, results, reports

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables & initial accounts exist
    await init_database()
    yield
    # Shutdown: clean up if needed

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="轻量级单医生医学影像智能分析平台 (后端服务)",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["认证鉴权 (Auth)"])
app.include_router(admin.router, prefix=f"{settings.API_V1_STR}/admin", tags=["管理员账号管理 (Admin)"])
app.include_router(studies.router, prefix=f"{settings.API_V1_STR}/studies", tags=["影像与检查 (Studies)"])
app.include_router(tasks.router, prefix=f"{settings.API_V1_STR}/tasks", tags=["分析调度与排队 (Tasks)"])
app.include_router(results.router, prefix=f"{settings.API_V1_STR}/results", tags=["病灶与组学结果 (Results)"])
app.include_router(reports.router, prefix=f"{settings.API_V1_STR}/reports", tags=["临床报告与协同 (Reports)"])

@app.get("/")
async def root():
    return {
        "system": settings.PROJECT_NAME,
        "status": "online",
        "max_gpu_concurrency": settings.MAX_GPU_CONCURRENT_TASKS,
        "api_docs": "/docs"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8020, reload=False)
