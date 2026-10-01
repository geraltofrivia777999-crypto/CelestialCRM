import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from celery.utils.log import get_task_logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models import IntegrationConnection, Status, SyncRun, SyncStatus
from app.services.alerts import AlertEngine
from app.services.keitaro_sync import KeitaroSyncEngine
from app.services.meta_comments import CommentJobEngine
from app.services.meta_launch import MetaLaunchPublisher
from app.services.meta_rules import MetaRuleEngine
from app.services.meta_sync import MetaSyncEngine
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)

# Воркер гоняет каждую задачу через asyncio.run() — то есть под новым event
# loop'ом. Соединения asyncpg привязаны к loop'у, в котором созданы: пул из
# общего движка отдал бы «мёртвое» соединение и RuntimeError «attached to a
# different loop». NullPool создаёт соединение под каждый сеанс — медленнее,
# но корректно при таком запуске.
_worker_engine = create_async_engine(settings.database_url, poolclass=NullPool)
WorkerSessionLocal = async_sessionmaker(
    _worker_engine, expire_on_commit=False, class_=AsyncSession
)


async def _run_sync(
    connection_id: str, run_id: str, mode: str, days: int | None = None
) -> dict:
    engine = KeitaroSyncEngine(WorkerSessionLocal)
    result = await engine.run(connection_id, run_id, mode, days)
    if result.get("status") == "success":
        # После коммита Keitaro просим общую очередь проверить правила. Не
        # отправляем Telegram из Keitaro-воркера: медленный чат не должен
        # удерживать критичную синхронизацию статистики.
        try:
            run_alerts.delay()
            result["alerts"] = {"queued": True}
        except Exception:  # noqa: BLE001 — минутный тик остаётся подстраховкой
            logger.exception("Failed to queue alerts after Keitaro sync")
    return result


async def _run_meta_sync(connection_id: str, run_id: str, mode: str) -> dict:
    engine = MetaSyncEngine(WorkerSessionLocal)
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
    days: int | None = None,
):
    logger.info(
        "Starting Keitaro sync connection=%s run=%s mode=%s days=%s",
        connection_id,
        run_id,
        mode,
        days,
    )
    return asyncio.run(_run_sync(connection_id, run_id, mode, days))


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
    time_limit=5400,
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
    return asyncio.run(MetaLaunchPublisher(WorkerSessionLocal).publish(launch_id, user_id))


@celery_app.task
def publish_due_meta_launches() -> dict:
    """Отправить в очередь заливы, у которых наступило запланированное время.

    Планировщик, а не отложенная задача в брокере: задача с eta на завтра не
    переживёт перезапуск воркера, и залив просто не состоится — молча.
    """
    return asyncio.run(_publish_due_launches())


async def _publish_due_launches() -> dict:
    from app.models import LaunchStatus, MetaLaunch

    async with WorkerSessionLocal() as db:
        # Заливы, застрявшие в publishing (упавший воркер, недопоставленная
        # задача) не должны висеть вечно: через пару часов переводим в failed,
        # чтобы человек увидел проблему и мог перезапустить.
        stuck = list(
            (
                await db.execute(
                    select(MetaLaunch).where(
                        MetaLaunch.status == LaunchStatus.publishing,
                        MetaLaunch.updated_at < datetime.now(UTC) - timedelta(hours=2),
                    )
                )
            ).scalars()
        )
        for launch in stuck:
            launch.status = LaunchStatus.failed
            launch.last_error = (
                "Публикация зависла: воркер не завершил её за 2 часа. "
                "Перезапустите публикацию."
            )
        if stuck:
            await db.commit()
            logger.warning("Released %s stuck publishing launch(es)", len(stuck))

    async with WorkerSessionLocal() as db:
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
            due.append((str(launch.id), str(launch.owner_id) if launch.owner_id else None))

    # Сначала ставим задачи, потом снимаем время и коммитим. Порядок «сначала
    # коммит» терял залив при падении между коммитом и enqueue: publish_at уже
    # пуст, статус draft — планировщик его больше не поднимет. При падении
    # между enqueue и коммитом задача может прийти дважды — от двойной
    # публикации защищает validate_launch (статус publishing), а зависшие
    # publishing-заливы подметает _expire_stuck_launches.
    for launch_id, owner_id in due:
        publish_meta_launch.delay(launch_id, owner_id)

    if due:
        async with WorkerSessionLocal() as db:
            for launch_id, _owner_id in due:
                launch = await db.get(MetaLaunch, uuid.UUID(launch_id))
                if launch:
                    launch.publish_at = None
            await db.commit()
        logger.info("Queued %s scheduled Meta launches", len(due))
    return {"queued": len(due)}


@celery_app.task(bind=True, acks_late=True)
def run_meta_comment_job(self, job_id: str) -> dict:
    """Загрузка или чистка комментариев одним заданием.

    Автоповтора нет намеренно: удаление комментария необратимо, и слепой ретрай
    после неясной ошибки прошёлся бы по списку второй раз. Задание помнит, что
    уже обработано, — повторный запуск делает человек, увидев, на чём встало.
    """
    logger.info("Running Meta comment job=%s", job_id)
    return asyncio.run(CommentJobEngine(WorkerSessionLocal).run(job_id))


@celery_app.task
def run_meta_rules() -> dict:
    if not settings.meta_rules_enabled:
        logger.info("Meta auto-rules are disabled")
        return {"rules": 0, "triggered": 0, "applied": 0}
    return asyncio.run(MetaRuleEngine(WorkerSessionLocal).run())


@celery_app.task
def run_meta_geo_rules() -> dict:
    """GEO-автоправила MetaAds v2 — у каждого воркспейса свой интервал."""
    if not settings.meta_rules_enabled:
        logger.info("Meta auto-rules are disabled")
        return {"rule_sets": 0, "triggered": 0, "paused": 0, "failed": 0}
    from app.api.routers.meta import client_for
    from app.services.meta_geo_rules import run_due

    return asyncio.run(run_due(WorkerSessionLocal, client_for))


@celery_app.task
def apply_due_budget_increases() -> dict:
    """Запланированные увеличения бюджета («Расширенный режим»)."""
    from app.services.budget_increase import apply_due_budget_increases as _apply
    from app.services.meta import MetaClient

    return asyncio.run(
        _apply(
            WorkerSessionLocal,
            lambda token, **kwargs: MetaClient(token, **kwargs),
        )
    )


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def run_alerts(self) -> dict:
    """Проверить правила «Утилит» и разослать уведомления — ТЗ 9."""
    if not settings.alerts_enabled:
        logger.info("Utilities alerts are disabled")
        return {"alerts": 0, "caps": 0}
    return asyncio.run(AlertEngine(WorkerSessionLocal).run())


QUEUED_RUN_TIMEOUT_MINUTES = 10
RUNNING_RUN_TIMEOUT_MINUTES = 30


async def _expire_stuck_runs(db, now: datetime) -> int:
    """Fail runs abandoned by a dead worker, otherwise they block scheduling forever."""
    meta_connections = select(IntegrationConnection.id).where(IntegrationConnection.kind == "meta")
    stuck = list(
        (
            await db.execute(
                select(SyncRun).where(
                    SyncRun.connection_id.in_(meta_connections),
                    SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
                    ((SyncRun.status == SyncStatus.queued) &
                     (SyncRun.created_at < now - timedelta(minutes=QUEUED_RUN_TIMEOUT_MINUTES))) |
                    ((SyncRun.status == SyncStatus.running) &
                     (func.coalesce(SyncRun.heartbeat_at, SyncRun.started_at) <
                      now - timedelta(minutes=RUNNING_RUN_TIMEOUT_MINUTES))),
                )
            )
        ).scalars()
    )
    meta_stuck_ids = {run.id for run in stuck}
    legacy_deadline = now - timedelta(minutes=120)
    stuck.extend((await db.execute(select(SyncRun).where(
        SyncRun.connection_id.not_in(meta_connections),
        SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
        func.coalesce(SyncRun.started_at, SyncRun.created_at) < legacy_deadline,
    ))).scalars())
    for run in stuck:
        was_queued = run.status == SyncStatus.queued
        run.status = SyncStatus.failed
        run.finished_at = now
        run.error = (
            "Задача Meta не дошла до воркера за 10 минут" if run.id in meta_stuck_ids and was_queued
            else "Синхронизация Meta не отвечала 30 минут" if run.id in meta_stuck_ids
            else "Синхронизация не завершилась за 120 минут"
        )
        details = dict(run.details or {})
        details["phase"] = "expired"
        run.details = details
    if stuck:
        logger.warning("Released %s stuck sync run(s)", len(stuck))
    return len(stuck)


async def _schedule_connections(kind: str, task) -> int:
    """Поставить синхронизацию тем подключениям, чей срок подошёл.

    Задание уходит воркеру только после коммита: он читает строку прогона из
    базы, и отправленное до фиксации он успевает не найти — задание завершалось
    впустую, строка навсегда оставалась «queued», а подключение с незавершённым
    прогоном планировщик больше не трогал.
    """
    pending: list[tuple[str, str]] = []
    now = datetime.now(UTC)
    async with WorkerSessionLocal() as db:
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
                last_sync_at = connection.last_sync_at
                if last_sync_at.tzinfo is None:
                    last_sync_at = last_sync_at.replace(tzinfo=UTC)
                due_at = last_sync_at + timedelta(
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
            pending.append((str(connection.id), str(run.id)))
        await db.commit()
    # Never enqueue before commit: an idle worker can pick the task before the
    # run row exists and leave a permanent "queued 0%" ghost.
    queued = 0
    for connection_id, run_id in pending:
        try:
            task.delay(connection_id, run_id, "incremental")
            queued += 1
        except Exception as exc:
            logger.exception("Could not enqueue %s sync %s", kind, run_id)
            async with WorkerSessionLocal() as db:
                run = await db.get(SyncRun, uuid.UUID(run_id))
                if run and run.status == SyncStatus.queued:
                    run.status = SyncStatus.failed
                    run.finished_at = datetime.now(UTC)
                    run.error = f"Не удалось поставить задачу в очередь: {type(exc).__name__}"
                    await db.commit()
    return queued


@celery_app.task
def schedule_keitaro_syncs() -> int:
    if not settings.keitaro_sync_enabled:
        logger.info("Scheduled Keitaro sync is disabled")
        return 0
    return asyncio.run(_schedule_connections("keitaro", sync_keitaro_connection))


@celery_app.task
def poll_keitaro_conversions() -> int:
    """Журнал конверсий отдельно от общей синхронизации — ради депозитов.

    Уведомление о депозите не должно ждать общего круга в четверть часа,
    поэтому журнал забирается своей лёгкой задачей раз в минуту.
    """
    if not settings.keitaro_sync_enabled:
        return 0
    return asyncio.run(_poll_keitaro_conversions_and_alert())


async def _poll_keitaro_conversions_and_alert() -> int:
    count = await KeitaroSyncEngine(WorkerSessionLocal).poll_conversions()
    if count:
        # Депозит уже записан — сразу будим outbox, но сам Keitaro-воркер не
        # блокируем Telegram-запросами.
        try:
            run_alerts.delay()
        except Exception:  # noqa: BLE001 — следующий минутный тик подстрахует
            logger.exception("Failed to queue alerts after conversion poll")
    return count


@celery_app.task
def schedule_meta_syncs() -> int:
    if not settings.meta_sync_enabled:
        logger.info("Scheduled Meta sync is disabled")
        return 0
    return asyncio.run(_schedule_connections("meta", sync_meta_connection))


@celery_app.task
def expire_stuck_syncs() -> int:
    """Independent watchdog: Meta's only worker may itself be blocked."""
    async def _run() -> int:
        async with WorkerSessionLocal() as db:
            count = await _expire_stuck_runs(db, datetime.now(UTC))
            await db.commit()
            return count
    return asyncio.run(_run())
