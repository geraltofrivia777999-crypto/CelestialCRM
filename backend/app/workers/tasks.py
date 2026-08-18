import asyncio
from datetime import UTC, datetime, timedelta

from celery.utils.log import get_task_logger
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import IntegrationConnection, Status, SyncRun, SyncStatus
from app.services.alerts import AlertEngine
from app.services.keitaro_sync import KeitaroSyncEngine
from app.services.meta_launch import MetaLaunchPublisher
from app.services.meta_rules import MetaRuleEngine
from app.services.meta_sync import MetaSyncEngine
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)


async def _run_sync(connection_id: str, run_id: str, mode: str) -> dict:
    engine = KeitaroSyncEngine(SessionLocal)
    return await engine.run(connection_id, run_id, mode)


async def _run_meta_sync(connection_id: str, run_id: str, mode: str) -> dict:
    engine = MetaSyncEngine(SessionLocal)
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


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def sync_meta_connection(
    self,
    connection_id: str,
    run_id: str,
    mode: str = "incremental",
):
    logger.info(
        "Starting Meta sync connection=%s run=%s mode=%s",
        connection_id,
        run_id,
        mode,
    )
    return asyncio.run(_run_meta_sync(connection_id, run_id, mode))


@celery_app.task(bind=True, acks_late=True)
def publish_meta_launch(self, launch_id: str, user_id: str | None = None):
    """Публикация залива (ТЗ 3.4).

    Автоповтора здесь нет намеренно, в отличие от синхронизации: задача создаёт
    объекты в кабинете, и слепой ретрай после неясной ошибки — прямой путь ко
    второй кампании. Публикация идемпотентна по записанным ID, поэтому повторный
    запуск делает человек, увидев, на чём именно всё остановилось.
    """
    logger.info("Publishing Meta launch=%s", launch_id)
    return asyncio.run(MetaLaunchPublisher(SessionLocal).publish(launch_id, user_id))


@celery_app.task
def publish_due_meta_launches() -> dict:
    """Отправить в очередь заливы, у которых наступило запланированное время.

    Планировщик, а не отложенная задача в брокере: задача с eta на завтра не
    переживёт перезапуск воркера, и залив просто не состоится — молча.
    """
    return asyncio.run(_publish_due_launches())


async def _publish_due_launches() -> dict:
    from app.models import LaunchStatus, MetaLaunch

    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(MetaLaunch).where(
                        MetaLaunch.publish_at.is_not(None),
                        MetaLaunch.publish_at <= datetime.now(UTC),
                        MetaLaunch.status == LaunchStatus.draft,
                    )
                )
            ).scalars()
        )
        due = []
        for launch in rows:
            # Время снимается сразу: иначе следующий тик планировщика поставит
            # тот же залив второй раз, пока публикация ещё идёт.
            launch.publish_at = None
            due.append((str(launch.id), str(launch.owner_id) if launch.owner_id else None))
        await db.commit()

    for launch_id, owner_id in due:
        publish_meta_launch.delay(launch_id, owner_id)
    if due:
        logger.info("Queued %s scheduled Meta launches", len(due))
    return {"queued": len(due)}


@celery_app.task
def run_meta_rules() -> dict:
    if not settings.meta_rules_enabled:
        logger.info("Meta auto-rules are disabled")
        return {"rules": 0, "triggered": 0, "applied": 0}
    return asyncio.run(MetaRuleEngine(SessionLocal).run())


@celery_app.task
def run_alerts() -> dict:
    """Проверить правила «Утилит» и разослать уведомления — ТЗ 9."""
    if not settings.alerts_enabled:
        logger.info("Utilities alerts are disabled")
        return {"alerts": 0, "caps": 0}
    return asyncio.run(AlertEngine(SessionLocal).run())


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
        logger.warning("Released %s stuck sync run(s)", len(stuck))
    return len(stuck)


async def _schedule_connections(kind: str, task) -> int:
    queued = 0
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        await _expire_stuck_runs(db, now)
        connections = list(
            (
                await db.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.kind == kind,
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
            task.delay(str(connection.id), str(run.id), "incremental")
            queued += 1
        await db.commit()
    return queued


@celery_app.task
def schedule_keitaro_syncs() -> int:
    if not settings.keitaro_sync_enabled:
        logger.info("Scheduled Keitaro sync is disabled")
        return 0
    return asyncio.run(_schedule_connections("keitaro", sync_keitaro_connection))


@celery_app.task
def schedule_meta_syncs() -> int:
    if not settings.meta_sync_enabled:
        logger.info("Scheduled Meta sync is disabled")
        return 0
    return asyncio.run(_schedule_connections("meta", sync_meta_connection))
