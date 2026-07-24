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
        }
    },
)
celery_app.autodiscover_tasks(["app.workers"])

