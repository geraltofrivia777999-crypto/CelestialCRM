from celery import Celery

from app.core.config import settings

celery_app = Celery("celestial", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "schedule-keitaro-syncs": {
            "task": "app.workers.tasks.schedule_keitaro_syncs",
            "schedule": max(settings.keitaro_sync_interval_minutes, 5) * 60,
        },
        "schedule-meta-syncs": {
            "task": "app.workers.tasks.schedule_meta_syncs",
            "schedule": max(settings.meta_sync_interval_minutes, 15) * 60,
        },
        # Правила считаются по уже загруженной статистике, поэтому чаще
        # синхронизации их гонять бессмысленно — цифры не изменятся.
        "run-meta-rules": {
            "task": "app.workers.tasks.run_meta_rules",
            "schedule": max(settings.meta_rules_interval_minutes, 15) * 60,
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

