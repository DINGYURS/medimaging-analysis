from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.api.deps import get_db, require_doctor
from app.services.feature_calculation import service
from app.services.feature_calculation.config import RadiomicsConfig
from app.services.feature_calculation.catalog import get_research_feature_catalog, radiomics_catalog_version
from app.services.feature_calculation.ibsi_features import IBSI_ENGINE_VERSION
from app.services.feature_calculation.models import CalculationRun
router = APIRouter(prefix='/{task_id}/feature-calculation', tags=['198项特征计算'])

class ConfigBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: RadiomicsConfig

class CreateBody(ConfigBody):
    snapshot_token: str = Field(min_length=1)

class InspectBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_version: Literal['legacy', 'physical-v1'] = 'legacy'

@router.get('/catalog')
async def catalog(task_id: int, db=Depends(get_db), user=Depends(require_doctor)):
    await service.resource(db, task_id, user.id, user.role)
    return {'catalog_version': radiomics_catalog_version(), 'engine_version': IBSI_ENGINE_VERSION, 'features': get_research_feature_catalog(), 'total_features_count': 198}

@router.post('/inputs')
async def inputs(task_id: int, body: InspectBody, db=Depends(get_db), user=Depends(require_doctor)):
    snap = await service.capture(db, task_id, user.id, user.role, body.source_version)
    return {'task_count': 1, 'lesion_count': len(snap['mapping']), 'excluded_lesions': snap.get('excluded_lesions', []), 'intensity': snap['intensity'], 'source': snap['source']}

@router.post('/confirm')
async def confirm(task_id: int, body: ConfigBody, db=Depends(get_db), user=Depends(require_doctor)):
    return await service.confirmation(db, task_id, user.id, user.role, body.config)

@router.post('/runs', status_code=202)
async def create(task_id: int, body: CreateBody, db=Depends(get_db), user=Depends(require_doctor)):
    return await service.create_run(db, task_id, user.id, user.role, body.config, body.snapshot_token)

@router.get('/runs')
async def runs(task_id: int, offset: int = Query(0, ge=0, le=2147483647), limit: int = Query(20, ge=1, le=100), db=Depends(get_db), user=Depends(require_doctor)):
    await service.resource(db, task_id, user.id, user.role)
    await service.recover(db)
    rows = (await db.execute(select(CalculationRun).where(CalculationRun.analysis_task_id == task_id).order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc()).offset(offset).limit(limit))).scalars().all()
    return [service.public_run(r) for r in rows]

@router.get('/runs/{run_id}')
async def run(task_id: int, run_id: str, db=Depends(get_db), user=Depends(require_doctor)):
    r = await service.get_run(db, task_id, run_id, user.id, user.role)
    data = service.public_run(r)
    if r.status in {'succeeded', 'partial'}:
        data['results'] = await service.results_for(db, r)
    return data

@router.get('/runs/{run_id}/export')
async def export(task_id: int, run_id: str, format: Literal['csv', 'excel']='excel', db=Depends(get_db), user=Depends(require_doctor)):
    r = await service.get_run(db, task_id, run_id, user.id, user.role)
    rows = await service.results_for(db, r)
    import asyncio
    data = await asyncio.to_thread(service.export_bytes, r, rows, format)
    ext = 'csv' if format == 'csv' else 'xlsx'
    return Response(data, media_type='text/csv; charset=utf-8' if ext == 'csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename="radiomics_{r.id}.{ext}"'})
