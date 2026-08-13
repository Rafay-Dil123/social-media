"""Outbox relay: move unprocessed outbox rows onto the Celery queue.

Handlers register themselves (typically in an ``AppConfig.ready``) mapping an
event type to a Celery task. ``drain_once`` fetches a batch of unprocessed rows,
publishes each to its task, and marks them processed.

Delivery is at-least-once (a crash between publish and mark re-publishes on the
next drain), so every handler task must be idempotent.
"""
from __future__ import annotations

from django.db import connections, transaction
from django.utils import timezone

from .models import Outbox

# event_type -> Celery task (anything exposing ``.delay(**payload)``)
DISPATCH: dict = {}


def register(event_type: str, task) -> None:
    DISPATCH[event_type] = task


def clear() -> None:
    """Test helper: reset the dispatch table."""
    DISPATCH.clear()


@transaction.atomic
def drain_once(batch: int = 200) -> int:
    qs = Outbox.objects.filter(processed_at__isnull=True).order_by("id")
    # SKIP LOCKED lets several relay workers drain in parallel (Postgres);
    # SQLite has no row locking, so we fall back to a plain read.
    if connections["default"].features.has_select_for_update_skip_locked:
        qs = qs.select_for_update(skip_locked=True)

    rows = list(qs[:batch])
    dispatched = []
    for row in rows:
        task = DISPATCH.get(row.event_type)
        if task is None:
            continue  # no handler yet; leave it for a later drain
        task.delay(**row.payload)
        dispatched.append(row.id)

    if dispatched:
        Outbox.objects.filter(id__in=dispatched).update(processed_at=timezone.now())
    return len(dispatched)
