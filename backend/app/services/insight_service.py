"""Durable insight jobs; cloud failures never change AnalysisTask status."""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from sqlalchemy import or_, select
from app.core.config import settings
from app.db.models import AIInsightRun, AnalysisTask
from app.db.session import AsyncSessionLocal
from app.services import ct_verified_insight as verified
from app.services.insight_client import SYSTEM_PROMPT, ProviderError, generate
from app.services.insight_payload import InputUnavailable, build_payload, load_task, payload_hash

logger = logging.getLogger(__name__)
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()

def prompt_for(payload):
    if payload.get("schema_version") == verified.VERSION:
        return verified.PROMPT_HASH, verified.VERSION
    return PROMPT_HASH, "clinical-insight-v2"


def digest(payload):
    source = {key: value for key, value in payload.items() if key != "verified_audit"}
    return payload_hash(source, prompt_for(source)[0], settings.LLM_MODEL, settings.LLM_API_BASE_URL)


async def build_job_payload(task):
    if task.study.modality == "CT":
        if task.status != "COMPLETED":
            raise InputUnavailable("影像分析尚未完成。")
        return await asyncio.to_thread(verified.snapshot, task)
    return await build_payload(task)


async def current_digest(task_id):
    async with AsyncSessionLocal() as db:
        task = await load_task(db, task_id)
        return digest(await build_job_payload(task)) if task else None

async def enqueue_task_insight(task_id: int, force: bool = False):
    if not settings.LLM_WORKER_ENABLED:
        return None
    async with AsyncSessionLocal() as db:
        task = await load_task(db, task_id)
        if not task or task.status != "COMPLETED":
            raise InputUnavailable("影像分析尚未完成。")
        try:
            snapshot = await build_job_payload(task)
            blocked = None
        except InputUnavailable as exc:
            snapshot, blocked = {}, str(exc)
        fingerprint = digest(snapshot)
        prompt_hash, prompt_version = prompt_for(snapshot)
    async with AsyncSessionLocal() as db:
        # Serialize submissions for the same task, including completed-cache checks.
        await db.execute(select(AnalysisTask.id).where(AnalysisTask.id == task_id).with_for_update())
        active = (await db.execute(select(AIInsightRun).where(
            AIInsightRun.active_task_id == task_id).with_for_update())).scalar_one_or_none()
        if active and active.input_hash == fingerprint and active.prompt_hash == prompt_hash:
            return active
        if active:
            active.status = "SUPERSEDED"
            active.active_task_id = None
            active.finished_at = datetime.utcnow()
            active.lease_token = None
            await db.flush()
        previous = (await db.execute(select(AIInsightRun).where(
            AIInsightRun.task_id == task_id).order_by(AIInsightRun.id.desc()).limit(1))).scalar_one_or_none()
        if previous and previous.input_hash == fingerprint and not force:
            await db.commit()
            return previous
        job = AIInsightRun(task_id=task_id, active_task_id=None if blocked else task_id,
            input_hash=fingerprint, prompt_hash=prompt_hash, prompt_version=prompt_version,
            model=settings.LLM_MODEL, status="BLOCKED" if blocked else "QUEUED",
            input_snapshot=snapshot, error_message=blocked, attempts=0,
            available_at=datetime.utcnow())
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

async def enqueue_safely(task_id: int):
    try:
        await enqueue_task_insight(task_id)
    except Exception:
        # Do not let an insight/table/configuration issue fail a completed imaging task.
        logger.warning("AI insight could not be queued for task %s", task_id)

async def claim_job():
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(AIInsightRun).where(or_(
            (AIInsightRun.status == "QUEUED") & (AIInsightRun.available_at <= now),
            (AIInsightRun.status == "RUNNING") & (AIInsightRun.lease_until < now),
        )).order_by(AIInsightRun.id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if not job:
            return None
        if job.attempts >= settings.LLM_MAX_ATTEMPTS:
            job.status, job.active_task_id = "FAILED", None
            job.error_message = "云端洞察任务已超过最大尝试次数，可手动重试。"
            job.finished_at = now
            await db.commit()
            return None
        job.status = "RUNNING"
        job.attempts += 1
        job.lease_token = uuid.uuid4().hex
        job.lease_until = now + timedelta(seconds=settings.LLM_TIMEOUT_SECONDS + 300)
        job.error_message = None
        await db.commit()
        return {"id": job.id, "task_id": job.task_id, "token": job.lease_token,
                "input_hash": job.input_hash, "snapshot": job.input_snapshot}

async def process_one() -> bool:
    claimed = await claim_job()
    if not claimed:
        return False
    result, usage, error, superseded, audit = None, {}, None, False, None
    try:
        source = claimed["snapshot"]
        expected = claimed["input_hash"]
        if digest(source) != expected or await current_digest(claimed["task_id"]) != expected:
            superseded = True
        else:
            if source.get("schema_version") == verified.VERSION:
                result, usage, audit = await verified.generate(source)
            else:
                result, usage = await generate(source, settings)
            superseded = await current_digest(claimed["task_id"]) != expected
    except InputUnavailable:
        superseded = True
    except ProviderError as exc:
        error = exc
    except Exception:
        error = ProviderError("洞察生成暂时不可用，请稍后重试。", True)
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(AIInsightRun).where(
            AIInsightRun.id == claimed["id"]).with_for_update())).scalar_one_or_none()
        if not job or job.status != "RUNNING" or job.lease_token != claimed["token"]:
            return True
        job.lease_token, job.lease_until = None, None
        if superseded:
            job.status, job.active_task_id = "SUPERSEDED", None
            job.error_message = "依据数据或模型配置已更新，请重新生成。"
            job.finished_at = datetime.utcnow()
        elif error:
            job.error_message = str(error)
            if error.retryable and job.attempts < settings.LLM_MAX_ATTEMPTS:
                job.status = "QUEUED"
                job.available_at = datetime.utcnow() + timedelta(seconds=5 * job.attempts)
            else:
                job.status, job.active_task_id = "FAILED", None
                job.finished_at = datetime.utcnow()
        else:
            job.result_json, job.usage_json = result, usage
            if audit is not None:
                job.input_snapshot = {**job.input_snapshot, "verified_audit": audit}
            job.status, job.active_task_id = "SUCCEEDED", None
            job.finished_at = datetime.utcnow()
        await db.commit()
    return True

async def run_worker():
    while True:
        try:
            if settings.LLM_API_KEY.get_secret_value().strip():
                worked = await process_one()
                if worked:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("AI insight worker temporarily unavailable")
        await asyncio.sleep(settings.LLM_POLL_INTERVAL_SECONDS)

async def latest_insight(task_id: int, db):
    if not settings.LLM_WORKER_ENABLED:
        return {"status": "NOT_ENABLED", "message": "AI 洞察已准备，等待数据库初始化及启用。", "result": None}
    job = (await db.execute(select(AIInsightRun).where(AIInsightRun.task_id == task_id)
                           .order_by(AIInsightRun.id.desc()).limit(1))).scalar_one_or_none()
    if not job:
        if not settings.LLM_API_KEY.get_secret_value().strip():
            return {"status": "WAITING_CONFIGURATION", "message": "请在服务器 backend/.env 中填写 LLM_API_KEY。", "result": None}
        return {"status": "NOT_GENERATED", "message": "该分析尚未生成 AI 洞察。", "result": None}
    display = job
    if not job.result_json:
        previous = (await db.execute(select(AIInsightRun).where(
            AIInsightRun.task_id == task_id, AIInsightRun.status == "SUCCEEDED",
            AIInsightRun.id < job.id).order_by(AIInsightRun.id.desc()).limit(1))).scalar_one_or_none()
        if previous:
            display = previous
    status, stale = job.status, False
    if status == "QUEUED" and not settings.LLM_API_KEY.get_secret_value().strip():
        status = "WAITING_CONFIGURATION"
    if display.result_json:
        try:
            task = await load_task(db, task_id)
            stale = task is None or digest(await build_job_payload(task)) != display.input_hash
        except InputUnavailable:
            stale = True
    if display.prompt_version == verified.VERSION:
        evidence = display.input_snapshot.get("verified_audit", {}).get("evidence", [])
    else:
        evidence = [value for lesion in display.input_snapshot.get("findings", {}).get("lesions", [])
                    for group in ("measurements", "radiomics") for value in lesion[group]]
    return {"id": job.id, "task_id": job.task_id, "status": status, "model": display.model,
            "message": job.error_message or ("请在服务器 backend/.env 中填写 LLM_API_KEY。" if status == "WAITING_CONFIGURATION" else ""),
            "result": display.result_json, "evidence": evidence, "is_stale": stale,
            "is_previous": display.id != job.id,
            "prompt_version": display.prompt_version, "attempts": job.attempts,
            "created_at": display.created_at, "finished_at": display.finished_at}
