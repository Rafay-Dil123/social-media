"""Celery application (Phase 4).

Tasks are autodiscovered from each app's ``tasks.py``. Settings are read from
Django with the ``CELERY_`` namespace. Running tasks inline (no broker) is
controlled by ``CELERY_TASK_ALWAYS_EAGER``.
"""
from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("social")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
