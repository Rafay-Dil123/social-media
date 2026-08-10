# Phase 4 — Crash-safe writes (transactional outbox + queue)

**Goal.** A created post's downstream work (fan-out, moderation, notifications)
can **never be lost**, even if the app crashes right after the DB commit.

**New:** `apps.common.Outbox`, a relay process, Celery + broker.

---

## 4.1 The problem this solves

Naive code does two non-atomic writes:

```python
post = Post.objects.create(...)   # committed to Postgres
fanout.delay(post.id)             # enqueued to a SEPARATE system (broker)
```

Crash **between** them → the post exists but nothing is ever fanned out. Two
systems can't be written atomically. The **outbox** makes the intent to enqueue
part of the same DB transaction.

---

## 4.2 Outbox model — `apps/common/models.py`

```python
class Outbox(models.Model):
    id = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=64)      # "post.created", ...
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)

    class Meta:
        db_table = "outbox"
        indexes = [
            models.Index(fields=["processed_at", "id"],
                         name="idx_outbox_unprocessed"),
        ]
```

## 4.3 Write side — enqueue *in the transaction*

Update `create_post` (Phase 1) so the outbox row commits atomically with the post:

```python
@transaction.atomic
def create_post(user, *, caption, visibility, media_ids):
    post = Post.objects.create(...)
    # ... attach media ...
    Outbox.objects.create(
        event_type="post.created",
        payload={"post_id": post.id, "author_id": str(user.id)},
    )
    return post          # post + outbox row commit together, or neither does
```

## 4.4 The relay — outbox → broker

A standalone loop (management command, or Celery beat every second). It reads
unprocessed rows, publishes the real job, and marks them done.

```python
# apps/common/relay.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.models import Outbox

DISPATCH = {}   # event_type -> celery task, registered by apps

def register(event_type, task):
    DISPATCH[event_type] = task


def drain_once(batch=200) -> int:
    # SKIP LOCKED lets multiple relay workers run without stepping on each other.
    rows = list(
        Outbox.objects.select_for_update(skip_locked=True)
        .filter(processed_at__isnull=True).order_by("id")[:batch]
    )
    for row in rows:
        task = DISPATCH.get(row.event_type)
        if task is None:
            continue
        task.delay(**row.payload)                 # publish to broker
    ids = [r.id for r in rows]
    if ids:
        Outbox.objects.filter(id__in=ids).update(
            processed_at=timezone.now(), attempts=models.F("attempts") + 1
        )
    return len(rows)
```

Wrap `drain_once` in a transaction per batch; run it in a tight loop with a short
sleep when empty. Because publish-then-mark can still crash *between* publish and
mark, the job must be **idempotent** (it may be delivered again). That's fine —
fan-out is idempotent.

> **Upgrade path:** replace polling with Postgres **logical replication / CDC**
> (Debezium) reading the outbox table's WAL — same table, lower latency, no
> polling. Start with polling; it's simple and correct.

## 4.5 Broker + worker config (Celery)

```python
# config/settings/base.py
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True            # redeliver if a worker dies mid-task
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1   # fair dispatch for long fan-out jobs
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.feed.tasks.fanout_post": {"queue": "fanout"},
    "apps.posts.tasks.process_media": {"queue": "media"},
}
```

`config/celery.py` with `app.autodiscover_tasks()`; separate worker pools per
queue so a slow fan-out storm doesn't starve media processing.

## 4.6 Delivery guarantees (the two crash points)

| crash point | guard |
|-------------|-------|
| between DB commit and enqueue | **outbox** (row survives, relay re-picks) |
| worker dies mid-job | **acks_late + retry** (broker redelivers) |
| relay dies between publish and mark | job re-published later → **idempotent** task |

Every consumer task must be **idempotent** and safe to run ≥1 time.

## 4.7 Tests

- `test_create_post_writes_outbox_row_in_same_txn`
- `test_outbox_row_survives_when_post_commits` (and rolls back together on error)
- `test_relay_dispatches_and_marks_processed`
- `test_relay_skip_locked_allows_parallel_drain`
- `test_task_is_idempotent_on_redelivery`

---

## Definition of done

`post.created` intent is durably recorded with the post; a relay reliably moves
it to the broker; workers run at-least-once with retries; every task is
idempotent. No user-visible change yet — this is the safety net Phase 5 rides on.
