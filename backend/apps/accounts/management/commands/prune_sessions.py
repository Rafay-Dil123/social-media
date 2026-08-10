"""Delete expired / revoked sessions so the table tracks only live logins.

Run on a schedule (cron, Celery beat, or a container job), e.g. nightly:

    python manage.py prune_sessions
    python manage.py prune_sessions --revoked-grace-days 30
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Session


class Command(BaseCommand):
    help = "Delete sessions whose absolute cap passed or that were revoked long ago."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--revoked-grace-days",
            type=int,
            default=30,
            help="Keep revoked sessions this many days for audit before deleting.",
        )

    def handle(self, *args, **options) -> None:
        now = timezone.now()
        grace = now - timedelta(days=options["revoked_grace_days"])

        qs = Session.objects.filter(
            Q(absolute_expiry__lt=now)
            | Q(expires_at__lt=now)
            | Q(revoked_at__lt=grace)
        )
        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Pruned {deleted} expired/revoked sessions."))
