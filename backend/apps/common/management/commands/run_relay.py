"""Continuously drain the transactional outbox onto the Celery queue.

Run alongside the web server and Celery workers:

    python manage.py run_relay
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.common import relay


class Command(BaseCommand):
    help = "Drain the outbox, publishing events to the task queue."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=0.5,
                            help="Seconds to sleep when the outbox is empty.")
        parser.add_argument("--batch", type=int, default=200)

    def handle(self, *args, **options):
        interval, batch = options["interval"], options["batch"]
        self.stdout.write("relay started; draining outbox...")
        while True:
            published = relay.drain_once(batch=batch)
            if published == 0:
                time.sleep(interval)
