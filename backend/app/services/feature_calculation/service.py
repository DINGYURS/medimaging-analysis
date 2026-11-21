from __future__ import annotations
import asyncio, csv, hashlib, io, json, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
from jose import jwt, JWTError
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import AnalysisTask, Study, Lesion, User
from .models import CalculationRun, CalculationResult
from .catalog import get_research_feature_catalog, radiomics_catalog_version
from .ibsi_features import IBSI_ENGINE_VERSION
from .config import RadiomicsConfig
from .inputs import inspect
from .engine import calculate_prepared_source
from .liver import study_profile
PROCESS_TOKEN = str(uuid.uuid4())
SLOTS = asyncio.Semaphore(2)
WORKERS = set()
INPUT_SLOTS = asyncio.Semaphore(1)
AUDIENCE = 'radiomics-input-confirmation-v1'

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False, default=lambda x: x.item() if isinstance(x, np.generic) else x.tolist() if isinstance(x, np.ndarray) else str(x))

def hashed(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def configuration_hash(config):
    return hashed(config.model_dump())
PIPELINE_HASH = hashed({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')})

def pipeline_hash():
    return PIPELINE_HASH

async def resource(db, task_id, user_id, role):
    user = (await db.execute(select(User).where(User.id == user_id).execution_options(populate_existing=True))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(403, '账号不可用')
    role = user.role
    if role not in {'ADMIN', 'DOCTOR'}:
        raise HTTPException(403, '无特征计算权限')
    pair = (await db.execute(select(AnalysisTask, Study).join(Study, Study.id == AnalysisTask.study_id).where(AnalysisTask.id == task_id).execution_options(populate_existing=True))).first()
    if pair is None or (role != 'ADMIN' and pair[0].doctor_id != user_id):
        raise HTTPException(404, '分析任务不存在或无权访问')
    (task, study) = pair
    if task.status != 'COMPLETED':
        raise HTTPException(409, '请先完成当前检查的分析')
    if task.doctor_id != study.doctor_id:
        raise HTTPException(409, '检查与任务所属医生不一致')
    lesions = (await db.execute(select(Lesion).where(Lesion.task_id == task.id).order_by(Lesion.lesion_index).execution_options(populate_existing=True))).scalars().all()
    if not lesions:
        raise HTTPException(422, '当前任务没有可计算病灶')
    return {'task_id': task.id, 'doctor_id': task.doctor_id, 'study': {'id': study.id, 'modality': study.modality, 'nifti_path': study.nifti_path, 'dicom_dir': study.dicom_dir, 'metadata_json': study.metadata_json or {}, 'study_description': study.study_description}, 'lesions': [{'id': l.id, 'lesion_index': l.lesion_index, 'mask_nifti_path': l.mask_nifti_path, 'center': [l.center_x, l.center_y, l.center_z], 'volume_mm3': l.volume_mm3} for l in lesions]}

async def capture(db, task_id, user_id, role, version, arrays=False):
    await db.commit()
    r = await resource(db, task_id, user_id, role)
    await db.commit()
    try:
        async with INPUT_SLOTS:
            data = await asyncio.to_thread(inspect, r, version, arrays)
    except (ValueError, OSError, TypeError) as e:
        raise HTTPException(422, '输入检查失败：' + str(e)) from e
    await db.commit()
    fresh = await resource(db, task_id, user_id, role)
    await db.commit()
    if r != fresh:
        raise HTTPException(409, '输入范围已变化，请重新确认')
    return data

async def confirmation(db, task_id, user_id, role, config):
    snapshot = await capture(db, task_id, user_id, role, config.source_version)
    if config.resegmentation.method == 'range' and config.resegmentation.intensity_basis.kind == 'calibrated':
        basis = config.resegmentation.intensity_basis
        if not snapshot['intensity'].get('unit') or basis.unit.casefold() != snapshot['intensity']['unit'].casefold():
            raise HTTPException(422, '重分割单位与源文件证据不一致')
    claims = {'aud': AUDIENCE, 'sub': str(user_id), 'task_id': task_id, 'configuration_hash': configuration_hash(config), 'input_hash': hashed(snapshot), 'pipeline_hash': pipeline_hash(), 'exp': datetime.now(timezone.utc) + timedelta(minutes=20)}
    return {'snapshot_token': jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM), 'input_hash': claims['input_hash'], 'configuration_hash': claims['configuration_hash'], 'task_count': 1, 'patient_count': 1, 'lesion_count': len(snapshot['mapping']), 'excluded_lesions': snapshot.get('excluded_lesions', []), 'intensity': snapshot['intensity'], 'confirmed_at': now().isoformat() + 'Z'}

def verify_receipt(token, task_id, user_id, config):
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM], audience=AUDIENCE)
        if claims['sub'] != str(user_id) or claims['task_id'] != task_id or claims['configuration_hash'] != configuration_hash(config) or (claims['pipeline_hash'] != pipeline_hash()):
            raise ValueError()
        return claims
    except (JWTError, KeyError, ValueError):
        raise HTTPException(409, '输入确认已过期或与当前配置不一致，请重新确认')

async def recover(db):
    cutoff = now() - timedelta(minutes=5)
    await db.execute(update(CalculationRun).where(CalculationRun.status.in_(['pending', 'running']), CalculationRun.heartbeat_at < cutoff).values(status='interrupted', active_task_id=None, finished_at=now(), error_summary={'reason': '计算服务租约已过期，请重新确认输入后提交'}))
    await db.commit()

async def create_run(db, task_id, user_id, role, config, token):
    claims = verify_receipt(token, task_id, user_id, config)
    snapshot = await capture(db, task_id, user_id, role, config.source_version)
    if hashed(snapshot) != claims['input_hash']:
        raise HTTPException(409, '源影像、掩膜或病灶范围已变化，请重新确认')
    await recover(db)
    run = CalculationRun(id=str(uuid.uuid4()), analysis_task_id=task_id, doctor_id=snapshot['resource']['doctor_id'], created_by_id=user_id, active_task_id=task_id, status='pending', config=config.model_dump(), catalog_version=radiomics_catalog_version(), engine_version=IBSI_ENGINE_VERSION, configuration_hash=claims['configuration_hash'], input_hash=claims['input_hash'], input_snapshot=snapshot, artifact_manifest={'pipeline_hash': claims['pipeline_hash']}, selected_count=len(config.features.keys), total_lesions=len(snapshot['mapping']), completed_lesions=0, worker_token=PROCESS_TOKEN, heartbeat_at=now(), created_at=now())
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, '当前分析已有正在执行的特征计算，请等待完成')
    task = asyncio.create_task(execute(run.id))
    WORKERS.add(task)
    task.add_done_callback(WORKERS.discard)
    return public_run(run)

async def heartbeat(run_id, stop):
    while not stop.is_set():
        async with AsyncSessionLocal() as db:
            await db.execute(update(CalculationRun).where(CalculationRun.id == run_id, CalculationRun.worker_token == PROCESS_TOKEN, CalculationRun.status.in_(['pending', 'running'])).values(heartbeat_at=now()))
            await db.commit()
        try:
            await asyncio.wait_for(stop.wait(), 15)
        except asyncio.TimeoutError:
            pass

def has_outputs(values, provenance):
    liver = provenance.get("liver_supplement") or {}
    return bool(values or liver.get("features") or liver.get("curves"))

async def execute(run_id):
    stop = asyncio.Event()
    pulse = asyncio.create_task(heartbeat(run_id, stop))
    try:
        async with SLOTS:
            async with AsyncSessionLocal() as db:
                run = await db.get(CalculationRun, run_id)
                if not run or run.status != 'pending' or run.worker_token != PROCESS_TOKEN:
                    return
                run.status = 'running'
                run.started_at = now()
                await db.commit()
                cfg = RadiomicsConfig.model_validate(run.config)
                (snapshot, arrays) = await capture(db, run.analysis_task_id, run.created_by_id, 'ADMIN', cfg.source_version, True)
                if hashed(snapshot) != run.input_hash:
                    raise ValueError('输入在执行前变化，已终止')
                (image, affine, spacing, evidence, rois) = arrays
                for (row, roi) in rois:
                    try:
                        result = await asyncio.to_thread(calculate_prepared_source, image, roi, spacing, affine, cfg, modality=snapshot['resource']['study']['modality'], intensity_unit=evidence.get('unit'), intensity_verified=bool(evidence.get('unit')), profile=study_profile(snapshot['resource']['study']), liver_profile=study_profile(snapshot['resource']['study'],snapshot.get('source')))
                        values = result['features']
                        missing = result['unavailable']
                        provenance = result['provenance']
                        error = None
                    except Exception as e:
                        values = {}
                        missing = {k: '当前病灶计算失败：' + str(e)[:250] for k in cfg.features.keys}
                        provenance = {}
                        error = str(e)[:1000]
                    state = (await db.execute(select(CalculationRun.status, CalculationRun.worker_token).where(CalculationRun.id == run_id))).first()
                    if not state or state.status != 'running' or state.worker_token != PROCESS_TOKEN:
                        raise ValueError('计算租约已失效')
                    db.add(CalculationResult(id=str(uuid.uuid4()), run_id=run.id, lesion_id=row['id'], lesion_index=row['lesion_index'], status='partial' if has_outputs(values,provenance) and (missing or (provenance.get('liver_supplement') or {}).get('unavailable')) else 'computed' if has_outputs(values,provenance) else 'failed', selected_count=len(cfg.features.keys), computed_count=len(values), unavailable_count=len(missing), features=values, unavailable=missing, provenance=json.loads(canonical(provenance)), error_message=error, created_at=now()))
                    run.completed_lesions += 1
                    run.heartbeat_at = now()
                    await db.commit()
                final = await capture(db, run.analysis_task_id, run.created_by_id, 'ADMIN', cfg.source_version)
                if hashed(final) != run.input_hash:
                    raise ValueError('输入在计算期间变化，结果不予发布')
                results = (await db.execute(select(CalculationResult).where(CalculationResult.run_id == run.id))).scalars().all()
                statuses = [r.status for r in results]
                run.status = 'succeeded' if all((s == 'computed' for s in statuses)) else 'partial' if any((has_outputs(r.features,r.provenance) for r in results)) else 'failed'
                run.error_summary = {'unavailable_features': sum((r.unavailable_count + len((r.provenance.get('liver_supplement') or {}).get('unavailable', {})) for r in results))}
                if run.status == 'failed':
                    run.error_summary['reason'] = '；'.join(list(dict.fromkeys((v for r in results for v in r.unavailable.values())))[:5])
                run.finished_at = now()
                run.active_task_id = None
                await db.commit()
    except BaseException as e:
        async with AsyncSessionLocal() as db:
            await db.execute(update(CalculationRun).where(CalculationRun.id == run_id, CalculationRun.worker_token == PROCESS_TOKEN, CalculationRun.status.in_(['pending', 'running'])).values(status='interrupted' if isinstance(e, asyncio.CancelledError) else 'failed', active_task_id=None, finished_at=now(), error_summary={'reason': str(e)[:1000]}))
            await db.commit()
        if isinstance(e, asyncio.CancelledError):
            raise
    finally:
        stop.set()
        await pulse

async def shutdown():
    tasks = list(WORKERS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

def public_run(run):
    return {
        'id': run.id, 'analysis_task_id': run.analysis_task_id,
        'status': run.status, 'selected_count': run.selected_count,
        'total_lesions': run.total_lesions, 'completed_lesions': run.completed_lesions,
        'created_at': run.created_at.isoformat() + 'Z',
        'started_at': run.started_at.isoformat() + 'Z' if run.started_at else None,
        'finished_at': run.finished_at.isoformat() + 'Z' if run.finished_at else None,
        'error_summary': run.error_summary,
        'excluded_lesions': (getattr(run, 'input_snapshot', None) or {}).get('excluded_lesions', []),
        # Historical views and exports use the saved snapshot, never the draft.
        'config': run.config, 'catalog_version': run.catalog_version,
        'engine_version': run.engine_version,
        'configuration_hash': run.configuration_hash, 'input_hash': run.input_hash,
    }

async def get_run(db, task_id, run_id, user_id, role):
    await resource(db, task_id, user_id, role)
    run = await db.get(CalculationRun, run_id)
    if not run or run.analysis_task_id != task_id:
        raise HTTPException(404, '计算记录不存在')
    if run.status in {'pending', 'running'}:
        await recover(db)
        await db.refresh(run)
    return run

async def results_for(db, run):
    if run.status not in {'succeeded', 'partial'}:
        raise HTTPException(409, '计算尚未完成或已失败，不能读取为正式结果')
    rows = (await db.execute(select(CalculationResult).where(CalculationResult.run_id == run.id).order_by(CalculationResult.lesion_index))).scalars().all()
    if len(rows) != run.total_lesions:
        raise HTTPException(409, '病灶结果范围已变化，不允许发布不完整结果')
    return [{'lesion_id': r.lesion_id, 'lesion_index': r.lesion_index, 'status': r.status, 'features': r.features, 'unavailable': r.unavailable, 'provenance': r.provenance} for r in rows]

def export_bytes(run, rows, format):
    keys = list(run.config['features']['keys'])
    supplements = [(r.get('provenance') or {}).get('liver_supplement') or {} for r in rows]
    liver_keys = sorted({key for item in supplements for group in ('features', 'unavailable', 'curves') for key in item.get(group, {})})
    header = ['lesion_id', 'lesion_index'] + keys + liver_keys
    def liver_value(item, key):
        if key in item.get('curves', {}): return canonical(item['curves'][key])
        return item.get('features', {}).get(key)
    matrix = [header] + [[r['lesion_id'], r['lesion_index']] + [r['features'].get(k) for k in keys] + [liver_value(item,k) for k in liver_keys] for r,item in zip(rows,supplements)]
    diagnostic = [['lesion_id', 'feature', 'reason']] + [[r['lesion_id'], k, v] for r,item in zip(rows,supplements) for k,v in {**r['unavailable'], **item.get('unavailable', {})}.items()]
    diagnostic.extend(
        [item['lesion_id'], '3D ROI', item['reason']]
        for item in (getattr(run, 'input_snapshot', None) or {}).get('excluded_lesions', [])
    )
    if format == 'csv':
        if any(supplements):
            matrix[0] += ['liver_supplement_metadata','liver_supplement_unavailable']
            for line,item in zip(matrix[1:],supplements):
                line += [canonical({k:item[k] for k in ['status','reason','version','profile','intensity_unit','input_mode','processing'] if k in item}), canonical(item.get('unavailable',{}))]
        out = io.StringIO(); csv.writer(out).writerows(matrix)
        return out.getvalue().encode('utf-8-sig')
    from openpyxl import Workbook
    wb = Workbook(); sheet = wb.active; sheet.title = 'features'
    for row in matrix: sheet.append(row)
    sheets = [('unavailable', diagnostic), ('configuration', [['field', 'value'], ['run_id', run.id], ['config', canonical(run.config)], ['input_hash', run.input_hash], ['engine_version', run.engine_version]])]
    if any(supplements):
        details=[['lesion_id','field','value']]
        for r,item in zip(rows,supplements):
            details.extend([[r['lesion_id'],key,canonical(value)] for key,value in item.items() if key not in {'curves','features','definitions','unavailable'}])
            details.extend([[r['lesion_id'],key,canonical(value)] for key,value in item.get('definitions',{}).items()])
        sheets.append(('liver_details',details))
        curves=[['lesion_id','curve','x','cdf']]
        for r,item in zip(rows,supplements):
            for name,curve in item.get('curves',{}).items():
                curves.extend([[r['lesion_id'],name,x,y] for x,y in zip(curve['x'],curve['y'])])
        sheets.append(('liver_curves',curves))
    for title,records in sheets:
        sheet = wb.create_sheet(title)
        for row in records: sheet.append(["'" + v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in row])
    out = io.BytesIO(); wb.save(out); return out.getvalue()
