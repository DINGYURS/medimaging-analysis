import os
import asyncio
import io
from datetime import datetime
from typing import List, Optional, Dict
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from PIL import Image
import numpy as np
import SimpleITK as sitk

from app.api.deps import get_db, require_doctor
from app.db.models import User, Study, Patient, AnalysisTask
from app.schemas.study import StudyActivityIn, StudyOut
from app.services.study_activity import record_study_operation
from app.services.study_service import process_and_save_uploaded_dicom

router = APIRouter()

# In-memory Volume & Mask Cache for sub-millisecond slice rendering
_VOLUME_CACHE: Dict[int, np.ndarray] = {}
_MASK_CACHE: Dict[int, np.ndarray] = {}

def get_cached_volume(study_id: int, nifti_path: str) -> np.ndarray:
    if study_id not in _VOLUME_CACHE:
        if not os.path.exists(nifti_path):
            raise HTTPException(status_code=404, detail="3D 体数据物理文件不存在")
        img = sitk.ReadImage(nifti_path)
        _VOLUME_CACHE[study_id] = sitk.GetArrayFromImage(img)
    return _VOLUME_CACHE[study_id]

def get_cached_mask(task_id: int, mask_path: str) -> Optional[np.ndarray]:
    if task_id not in _MASK_CACHE:
        if not mask_path or not os.path.exists(mask_path):
            return None
        img = sitk.ReadImage(mask_path)
        _MASK_CACHE[task_id] = sitk.GetArrayFromImage(img)
    return _MASK_CACHE[task_id]

def stream_file_with_range(request: Request, file_path: str, media_type: str = "application/gzip") -> Response:
    """Helper to serve files with HTTP 206 Partial Content support for 3D Viewers."""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="请求的体数据文件不存在")

    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("Range")

    if range_header:
        try:
            byte_range = range_header.replace("bytes=", "").split("-")
            start = int(byte_range[0])
            end = int(byte_range[1]) if byte_range[1] else file_size - 1
            if start >= file_size:
                raise HTTPException(status_code=416, detail="Range Not Satisfiable")
            chunk_size = end - start + 1
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Range header")

        def iter_chunk():
            with open(file_path, "rb") as f:
                f.seek(start)
                bytes_left = chunk_size
                while bytes_left > 0:
                    read_len = min(64 * 1024, bytes_left)
                    data = f.read(read_len)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
        }
        return StreamingResponse(iter_chunk(), status_code=206, headers=headers, media_type=media_type)

    def iter_full():
        with open(file_path, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
    }
    return StreamingResponse(iter_full(), status_code=200, headers=headers, media_type=media_type)

@router.post("/upload", response_model=StudyOut)
async def upload_dicom_study(
    file: UploadFile = File(..., description="DICOM 序列压缩包 (.zip) 或单个切片文件"),
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """上传 DICOM 序列，自动转换为 3D NIfTI 并绑定当前医生 (策略 A)."""
    study = await process_and_save_uploaded_dicom(file=file, doctor=doctor, db=db)
    res = await db.execute(
        select(Study).options(selectinload(Study.patient), selectinload(Study.tasks)).where(Study.id == study.id)
    )
    return res.scalar_one()

@router.get("", response_model=List[StudyOut])
@router.get("/", response_model=List[StudyOut], include_in_schema=False)
async def list_doctor_studies(
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """获取当前医生拥有的所有检查 (策略 A 数据隔离)."""
    query = (
        select(Study)
        .options(selectinload(Study.patient), selectinload(Study.tasks))
        .order_by(Study.created_at.desc())
    )
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)

    res = await db.execute(query)
    return res.scalars().all()

@router.get("/{study_id}", response_model=StudyOut)
async def get_study_detail(
    study_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    query = (
        select(Study)
        .options(selectinload(Study.patient), selectinload(Study.tasks))
        .where(Study.id == study_id)
    )
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)

    res = await db.execute(query)
    study = res.scalar_one_or_none()
    if not study:
        raise HTTPException(status_code=404, detail="检查记录不存在或无权访问")
    return study

@router.get("/{study_id}/series")
async def get_related_series(
    study_id: int,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor),
):
    study = await get_study_detail(study_id, db, doctor)
    group = (study.metadata_json or {}).get("source_study_instance_uid")
    if not group:
        return []
    candidates = (await db.execute(select(Study.id, Study.metadata_json, Study.slice_count).where(
        Study.doctor_id == study.doctor_id, Study.patient_id == study.patient_id,
    ))).all()
    related = [
        {"id": row.id, "label": metadata["sequence_label"], "channel": metadata["nnunet_channel"],
         "slice_count": row.slice_count}
        for row in candidates
        if (metadata := row.metadata_json or {}).get("source_study_instance_uid") == group
        and metadata.get("sequence_label") and isinstance(metadata.get("nnunet_channel"), int)
    ]
    return sorted(related, key=lambda row: (row["channel"], row["id"]))


@router.post("/{study_id}/activity", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def record_study_activity(
    study_id: int,
    payload: StudyActivityIn,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor),
):
    occurred_at = datetime.utcnow()
    owner_query = select(Study.doctor_id).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        owner_query = owner_query.where(Study.doctor_id == doctor.id)

    owner_id = (await db.execute(owner_query)).scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(status_code=404, detail="检查记录不存在或无权访问")

    if payload.task_id is not None:
        result_query = select(AnalysisTask.id).where(
            AnalysisTask.id == payload.task_id,
            AnalysisTask.study_id == study_id,
            AnalysisTask.status == "COMPLETED",
        )
        if doctor.role != "ADMIN":
            result_query = result_query.where(AnalysisTask.doctor_id == doctor.id)
        if (await db.execute(result_query)).scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="分析结果不存在或无权访问")

    if owner_id == doctor.id:
        await record_study_operation(db, study_id, doctor.id, occurred_at)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{study_id}/volume")
async def stream_study_volume(
    study_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """流式获取 3D NIfTI 体数据 (支持 HTTP Range 请求)."""
    query = select(Study).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)

    res = await db.execute(query)
    study = res.scalar_one_or_none()
    if not study or not study.nifti_path:
        raise HTTPException(status_code=404, detail="检查记录或 3D 体数据文件不存在")

    return stream_file_with_range(request, study.nifti_path, media_type="application/gzip")

_SLICE_IMG_CACHE: Dict[str, bytes] = {}

@router.get("/{study_id}/slice/{slice_index}")
async def get_rendered_slice(
    study_id: int,
    slice_index: int,
    ww: float = Query(1500.0, description="窗宽 (Window Width)"),
    wc: float = Query(-600.0, description="窗位 (Window Center)"),
    overlay_mask: bool = Query(False, description="是否叠加病灶掩膜"),
    opacity: float = Query(0.5, description="掩膜透明度 0.0 - 1.0"),
    task_id: Optional[int] = Query(None, description="关联的分析任务 ID"),
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """
    高保真临床切片动态渲染接口 (毫秒级内存缓存 + 客户端持久缓存)：
    按医生指定的窗宽/窗位进行动态映射，支持 3D Mask 病灶叠层半透明渲染。
    """
    query = select(Study).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)
    res = await db.execute(query)
    study = res.scalar_one_or_none()
    if not study or not study.nifti_path:
        raise HTTPException(status_code=404, detail="检查体数据未找到")

    slice_cache_key = f"{study_id}_{slice_index}_{int(ww)}_{int(wc)}_{overlay_mask}_{int(opacity*100)}_{task_id or 0}"
    if slice_cache_key in _SLICE_IMG_CACHE:
        return Response(
            content=_SLICE_IMG_CACHE[slice_cache_key],
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400, immutable", "Vary": "Authorization", "X-Slice-Cache": "HIT"}
        )

    vol = get_cached_volume(study.id, study.nifti_path)
    total_slices = vol.shape[0]

    # Convert 1-based index (from UI) to 0-based index
    idx = max(0, min(total_slices - 1, slice_index - 1))
    source_slice = vol[idx]

    # Apply display windowing
    low = wc - ww / 2.0
    gray = np.clip((source_slice - low) / ww * 255.0, 0, 255).astype(np.uint8)

    # If overlay_mask is requested, check mask
    if overlay_mask and task_id:
        task_res = await db.execute(
            select(AnalysisTask).options(selectinload(AnalysisTask.lesions)).where(
                AnalysisTask.id == task_id, AnalysisTask.study_id == study.id,
            )
        )
        task = task_res.scalar_one_or_none()
        if task and task.lesions:
            mask_path = task.lesions[0].mask_nifti_path
            mask_vol = get_cached_mask(task.id, mask_path)
            if mask_vol is not None and mask_vol.shape == vol.shape:
                mask_slice = mask_vol[idx] > 0
                if mask_slice.any():
                    rgb = np.stack([gray, gray, gray], axis=-1)
                    op = max(0.1, min(0.9, opacity))
                    # Alpha blend with vivid red (239, 68, 68)
                    rgb[mask_slice] = (
                        rgb[mask_slice] * (1.0 - op) + np.array([239, 68, 68]) * op
                    ).astype(np.uint8)
                    im = Image.fromarray(rgb)
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=90)
                    jpeg_bytes = buf.getvalue()
                    _SLICE_IMG_CACHE[slice_cache_key] = jpeg_bytes
                    return Response(
                        content=jpeg_bytes,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400, immutable", "Vary": "Authorization", "X-Slice-Cache": "MISS"}
                    )

    # Grayscale image
    im = Image.fromarray(gray)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()
    _SLICE_IMG_CACHE[slice_cache_key] = jpeg_bytes
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400, immutable", "Vary": "Authorization", "X-Slice-Cache": "MISS"}
    )


_MASK_SLICE_CACHE: Dict[str, bytes] = {}

@router.get("/{study_id}/mask-slice/{slice_index}")
async def get_mask_slice_image(
    study_id: int,
    slice_index: int,
    task_id: Optional[int] = Query(None, description="分析任务 ID"),
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """Return a transparent overlay in the original sequence's pixel grid."""
    query = select(Study).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)
    study = await db.scalar(query)
    if not study or not study.nifti_path:
        raise HTTPException(status_code=404, detail="检查体数据未找到")

    headers = {"Cache-Control": "private, max-age=86400, immutable", "Vary": "Authorization"}
    cache_key = f"{study_id}_{slice_index}_{task_id or 0}"
    if cache_key in _MASK_SLICE_CACHE:
        return Response(_MASK_SLICE_CACHE[cache_key], media_type="image/png", headers=headers)

    vol = get_cached_volume(study.id, study.nifti_path)
    rgba = np.zeros((*vol.shape[1:], 4), dtype=np.uint8)
    if task_id is not None:
        task = await db.scalar(select(AnalysisTask).options(selectinload(AnalysisTask.lesions)).where(
            AnalysisTask.id == task_id, AnalysisTask.study_id == study.id,
        ))
        if task and task.lesions:
            path = task.lesions[0].mask_nifti_path
            mask_vol = get_cached_mask(task.id, path) if path else None
            if mask_vol is not None and mask_vol.shape == vol.shape:
                idx = max(0, min(mask_vol.shape[0] - 1, slice_index - 1))
                rgba[mask_vol[idx] > 0] = [239, 68, 68, 255]

    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", optimize=True)
    png_bytes = buffer.getvalue()
    _MASK_SLICE_CACHE[cache_key] = png_bytes
    return Response(png_bytes, media_type="image/png", headers=headers)


_PACK_CACHE: Dict[str, bytes] = {}
VOLUME_PACK_CACHE_DIR = Path("/data/medimaging-analysis/storage/cache/volume_packs")
VOLUME_PACK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/{study_id}/volume-pack")
async def get_study_volume_pack(
    study_id: int,
    ww: float = Query(1500.0, description="窗宽"),
    wc: float = Query(-600.0, description="窗位"),
    task_id: Optional[int] = Query(None, description="分析任务 ID"),
    db: AsyncSession = Depends(get_db),
    doctor: User = Depends(require_doctor)
):
    """
    全量体数据与掩膜高密度预加载包：
    将整套 268 层 CT 序列按窗宽窗位量化为 uint8 矩阵，并打包 3D Mask，
    通过 Gzip 压缩并持久化到 SSD 磁盘缓存，极速秒级响应。
    """
    cache_key = f"{study_id}_{int(ww)}_{int(wc)}_{task_id}"
    if cache_key in _PACK_CACHE:
        return Response(
            content=_PACK_CACHE[cache_key],
            media_type="application/octet-stream",
            headers={"Content-Encoding": "gzip", "X-Cache": "MEM_HIT"}
        )

    disk_cache_file = VOLUME_PACK_CACHE_DIR / f"study_{study_id}_ww{int(ww)}_wc{int(wc)}_task{task_id or 0}.pack.gz"
    if disk_cache_file.exists():
        try:
            with open(disk_cache_file, "rb") as f:
                content = f.read()
            _PACK_CACHE[cache_key] = content
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Content-Encoding": "gzip", "X-Cache": "DISK_HIT"}
            )
        except Exception:
            pass

    query = select(Study).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)
    res = await db.execute(query)
    study = res.scalar_one_or_none()
    if not study or not study.nifti_path:
        raise HTTPException(status_code=404, detail="检查体数据未找到")

    vol = get_cached_volume(study.id, study.nifti_path)
    total_slices, h, w = vol.shape

    # Fast Windowing to uint8
    low = wc - ww / 2.0
    vol_uint8 = np.clip((vol - low) / ww * 255.0, 0, 255).astype(np.uint8)

    # Check mask
    has_mask = 0
    mask_bytes = b""
    if task_id:
        task_res = await db.execute(
            select(AnalysisTask).options(selectinload(AnalysisTask.lesions)).where(AnalysisTask.id == task_id)
        )
        task = task_res.scalar_one_or_none()
        if task and task.lesions:
            mask_path = task.lesions[0].mask_nifti_path
            mask_vol = get_cached_mask(task.id, mask_path)
            if mask_vol is not None:
                has_mask = 1
                mask_uint8 = (mask_vol > 0).astype(np.uint8)
                mask_bytes = mask_uint8.tobytes()

    import struct, gzip
    # 16-byte header: uint32 total_slices, uint32 width, uint32 height, uint32 has_mask
    header = struct.pack('<IIII', total_slices, w, h, has_mask)
    payload = header + vol_uint8.tobytes() + mask_bytes
    compressed = gzip.compress(payload, compresslevel=1)
    
    # Cache in memory and disk
    _PACK_CACHE[cache_key] = compressed
    try:
        tmp_file = disk_cache_file.with_suffix(".tmp")
        with open(tmp_file, "wb") as f:
            f.write(compressed)
        tmp_file.replace(disk_cache_file)
    except Exception:
        pass

    return Response(
        content=compressed,
        media_type="application/octet-stream",
        headers={"Content-Encoding": "gzip", "X-Cache": "MISS"}
    )


@router.get("/{study_id}/intensity-unit")
async def get_study_intensity_unit(study_id: int, db: AsyncSession = Depends(get_db), doctor: User = Depends(require_doctor)):
    """Verify saved voxel units from original DICOM without modifying the database."""
    query = select(Study).where(Study.id == study_id)
    if doctor.role != "ADMIN":
        query = query.where(Study.doctor_id == doctor.id)
    study = (await db.execute(query)).scalar_one_or_none()
    if not study:
        raise HTTPException(status_code=404, detail="检查记录不存在或无权访问")
    from src.dicom_io import verify_dicom_intensity_unit
    metadata = study.metadata_json or {}
    return await asyncio.to_thread(verify_dicom_intensity_unit, study.dicom_dir, study.nifti_path, metadata.get('series_instance_uid'))
