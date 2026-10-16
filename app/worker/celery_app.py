"""Celery application: broker settings, beat schedule and task registration."""

from celery import Celery
from celery.schedules import crontab

from app.config import Settings, get_settings
from app.worker.tasks import COLLECT_TASK_NAME, COLLECTION_PERIOD_SECONDS, register_tasks


def create_celery_app(settings: Settings) -> Celery:
    """Build a Celery app configured from the given settings."""
    app = Celery("deribit_prices", broker=settings.redis_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "collect-prices": {
                "task": COLLECT_TASK_NAME,
                # Every minute at :00, so a run starts at the slot boundary and queue
                # delay cannot push it into the neighbouring slot.
                "schedule": crontab(),
                # A run queued while the worker was down is useless once its slot has
                # passed; dropping it avoids a burst of stale tasks on restart.
                "options": {"expires": COLLECTION_PERIOD_SECONDS},
            },
        },
    )
    register_tasks(app, settings)
    return app


# The Celery CLI (`celery -A app.worker.celery_app ...`) looks for a ready-made Celery
# object in this module and cannot call a factory, so this is the single module-level
# object. It is built by the factory above; nothing else in the project uses it.
celery = create_celery_app(get_settings())
