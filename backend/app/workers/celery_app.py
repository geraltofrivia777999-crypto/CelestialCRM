from celery import Celery
from celery.schedules import crontab

from app.core.clock import business_now, meta_rules_step_minutes
from app.core.config import settings


def clock_aligned(step: int) -> crontab:
    """Расписание «каждые N минут» по часам, а не от запуска beat.

    Интервал в секундах отсчитывается от старта процесса: при старте в 14:17
    прогоны шли в :17 и :47 и никогда не попадали в полночь. Часы — московские,
    как и расписания самих правил.
    """
    if step < 60:
        return crontab(minute=f"*/{step}", nowfun=business_now)
    if step < 1440:
        return crontab(minute=0, hour=f"*/{step // 60}", nowfun=business_now)
    return crontab(minute=0, hour=0, nowfun=business_now)


celery_app = Celery("celestial", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Долгий вход в Meta через браузер не должен задерживать цифры Keitaro.
    # Очереди обслуживаются отдельными процессами в docker-compose.yml.
    task_routes={
        "app.workers.tasks.sync_keitaro_connection": {"queue": "keitaro"},
        "app.workers.tasks.schedule_keitaro_syncs": {"queue": "keitaro"},
        "app.workers.tasks.poll_keitaro_conversions": {"queue": "keitaro"},
        "app.workers.tasks.sync_meta_connection": {"queue": "meta"},
        "app.workers.tasks.schedule_meta_syncs": {"queue": "meta"},
    },
    beat_schedule={
        "schedule-keitaro-syncs": {
            "task": "app.workers.tasks.schedule_keitaro_syncs",
            # Планировщик только проверяет, какие подключения уже просрочены.
            # Частый лёгкий тик позволяет соблюдать интервал конкретного
            # подключения (например, 15 минут), а не глобальный интервал beat.
            "schedule": 60,
            "options": {"queue": "keitaro"},
        },
        # Журнал конверсий — отдельно и часто: депозит должен доехать до чата
        # за минуту, а не за общий круг синхронизации.
        "poll-keitaro-conversions": {
            "task": "app.workers.tasks.poll_keitaro_conversions",
            "schedule": max(settings.keitaro_conversions_interval_minutes, 1) * 60,
            "options": {"queue": "keitaro"},
        },
        "schedule-meta-syncs": {
            "task": "app.workers.tasks.schedule_meta_syncs",
            "schedule": max(settings.meta_sync_interval_minutes, 15) * 60,
            "options": {"queue": "meta"},
        },
        # Правила считаются по уже загруженной статистике, поэтому чаще
        # синхронизации их гонять бессмысленно — цифры не изменятся.
        "run-meta-rules": {
            "task": "app.workers.tasks.run_meta_rules",
            "schedule": clock_aligned(meta_rules_step_minutes()),
        },
        # Алерты гоняются чаще правил Meta: их смысл в том, чтобы узнать о
        # проблеме сегодня, а не через полчаса. Пауза между срабатываниями
        # задана у каждого правила отдельно.
        # Запланированные заливы: проверяем раз в минуту, потому что время
        # залива баер указывает с точностью до минуты.
        "publish-due-meta-launches": {
            "task": "app.workers.tasks.publish_due_meta_launches",
            "schedule": 60,
        },
        "run-alerts": {
            "task": "app.workers.tasks.run_alerts",
            "schedule": max(settings.alerts_interval_minutes, 1) * 60,
        },
        # Запланированные увеличения бюджета: окно периодов с точностью до
        # минуты, поэтому проверяем часто и применяем ровно один раз.
        "apply-due-budget-increases": {
            "task": "app.workers.tasks.apply_due_budget_increases",
            "schedule": 60,
        },
    },
)
celery_app.autodiscover_tasks(["app.workers"])
