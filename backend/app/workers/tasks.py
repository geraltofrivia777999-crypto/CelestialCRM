import asyncio
from datetime import UTC, datetime, timedelta

from celery.utils.log import get_task_logger
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import IntegrationConnection, Status, SyncRun, SyncStatus
from app.services.keitaro_sync import KeitaroSyncEngine
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)


async def _run_sync(connection_id: str, run_id: str, mode: str) -> dict:
    engine = KeitaroSyncEngine(SessionLocal)
    return await engine.run(connection_id, run_id, mode)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def sync_keitaro_connection(
    self,
    connection_id: str,
    run_id: str,
    mode: str = "incremental",
):
    logger.info(
        "Starting Keitaro sync connection=%s run=%s mode=%s",
        connection_id,
        run_id,
        mode,
    )
    return asyncio.run(_run_sync(connection_id, run_id, mode))


STUCK_RUN_TIMEOUT_MINUTES = 120


async def _expire_stuck_runs(db, now: datetime) -> int:
    """Fail runs abandoned by a dead worker, otherwise they block scheduling forever."""
    deadline = now - timedelta(minutes=STUCK_RUN_TIMEOUT_MINUTES)
    stuck = list(
        (
            await db.execute(
                select(SyncRun).where(
                    SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
                    func.coalesce(SyncRun.started_at, SyncRun.created_at) < deadline,
                )
            )
        ).scalars()
    )
    for run in stuck:
        run.status = SyncStatus.failed
        run.finished_at = now
        run.error = (
            f"TimeoutError: sync did not finish within "
            f"{STUCK_RUN_TIMEOUT_MINUTES} minutes and was released"
        )
        details = dict(run.details or {})
        details["phase"] = "expired"
        run.details = details
    if stuck:
        logger.warning("Released %s stuck Keitaro sync run(s)", len(stuck))
    return len(stuck)


async def _schedule_connections() -> int:
    if not settings.keitaro_sync_enabled:
        logger.info("Scheduled Keitaro sync is disabled")
        return 0
    queued = 0
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        await _expire_stuck_runs(db, now)
        connections = list(
            (
                await db.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.kind == "keitaro",
                        IntegrationConnection.status == Status.active,
                    )
                )
            ).scalars()
        )
        for connection in connections:
            running = await db.scalar(
                select(SyncRun).where(
                    SyncRun.connection_id == connection.id,
                    SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
                )
            )
            if running:
                continue
            if connection.last_sync_at:
                due_at = connection.last_sync_at + timedelta(
                    minutes=max(connection.sync_interval_minutes, 5)
                )
                if due_at > now:
                    continue
            run = SyncRun(
                connection_id=connection.id,
                mode="incremental",
                status=SyncStatus.queued,
                details={"phase": "queued", "source": "scheduler"},
            )
            db.add(run)
            await db.flush()
            sync_keitaro_connection.delay(
                str(connection.id),
                str(run.id),
                "incremental",
            )
            queued += 1
        await db.commit()
    return queued


@celery_app.task
def schedule_keitaro_syncs() -> int:
    return asyncio.run(_schedule_connections())
