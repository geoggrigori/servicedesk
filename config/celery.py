"""Celery application and beat schedule."""

import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("servicedesk")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    """Sweep for SLA breaches on a fixed cadence."""
    sender.add_periodic_task(
        crontab(minute=f"*/{settings.SLA_SWEEP_MINUTES}"),
        sender.signature("tickets.tasks.sweep_sla_breaches"),
        name="sweep SLA breaches",
    )
