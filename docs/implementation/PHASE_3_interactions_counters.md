# Phase 3 — Interactions + counters (Redis)

**Goal.** Likes (and comments, same shape) with counts that survive a viral post
without hot-row locking.

**New app:** `apps.interactions`. **New shared helper:** `apps/common/redis.py`.

---

## 3.1 Shared Redis client — `apps/common/redis.py`

```python
from __future__ import annotations

import redis
from django.conf import settings

_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


def redis_client() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)
```

`config/settings/base.py`: `REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")`.

---

## 3.2 Model — `apps/interactions/models.py`

```python
from __future__ import annotations

from django.conf import settings
from django.db import models


class Like(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="likes")
    post = models.ForeignKey("posts.Post", on_delete=models.CASCADE,
                             related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)   # unlike = soft delete

    class Meta:
        db_table = "likes"
        constraints = [
            # ONE active like per (user, post); history preserved via new rows.
            models.UniqueConstraint(
                fields=["user", "post"], condition=models.Q(deleted_at__isnull=True),
                name="uniq_active_like",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "deleted_at"]),   # "who liked" + count reseed
            models.Index(fields=["user", "deleted_at"]),   # "posts I liked"
        ]
```

---

## 3.3 Service — toggle + counter

```python
# apps/interactions/services.py
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.redis import redis_client
from .models import Like

LIKES_KEY = "post:{}:likes"


@transaction.atomic
def like(user, post_id: int) -> None:
    """Idempotent: liking an already-liked post is a no-op (count unchanged)."""
    active = Like.objects.select_for_update().filter(
        user=user, post_id=post_id, deleted_at__isnull=True).first()
    if active:
        return
    # Revive a soft-deleted row if present, else insert.
    revived = Like.objects.filter(user=user, post_id=post_id).order_by("-id").first()
    if revived and revived.deleted_at is not None:
        revived.deleted_at = None
        revived.created_at = timezone.now()
        revived.save(update_fields=["deleted_at", "created_at"])
    else:
        try:
            Like.objects.create(user=user, post_id=post_id)
        except IntegrityError:
            return  # lost a race; the other tx created the active like
    redis_client().incr(LIKES_KEY.format(post_id))


@transaction.atomic
def unlike(user, post_id: int) -> None:
    active = Like.objects.select_for_update().filter(
        user=user, post_id=post_id, deleted_at__isnull=True).first()
    if not active:
        return
    active.deleted_at = timezone.now()
    active.save(update_fields=["deleted_at"])
    redis_client().decr(LIKES_KEY.format(post_id))
```

> **Why partial-unique + revive.** The partial unique index lets many
> historical (user, post) rows exist while guaranteeing one *active* one. On
> re-like we revive the latest row (or insert). Concurrency is handled by
> `select_for_update` + the DB constraint as the final arbiter.

---

## 3.4 Reading the count (with cache-miss reseed)

```python
def like_count(post_id: int) -> int:
    r = redis_client()
    key = LIKES_KEY.format(post_id)
    val = r.get(key)
    if val is not None:
        return int(val)
    # Cache miss / Redis lost -> reseed from the source of truth.
    n = Like.objects.filter(post_id=post_id, deleted_at__isnull=True).count()
    r.set(key, n)                      # optionally EX so cold posts expire
    return n
```

For hydrating many posts at once (Phase 6): `MGET post:{id}:likes ...` and reseed
only the misses in one `GROUP BY` query.

---

## 3.5 Reconciler — flush Redis → Postgres (durable mirror)

Keeps `posts.like_count` roughly current so analytics/sorting don't need Redis,
and gives a durable fallback.

```python
# Celery beat, every ~60s
@shared_task
def reconcile_like_counts():
    r = redis_client()
    for key in r.scan_iter(match="post:*:likes", count=500):
        post_id = int(key.split(":")[1])
        val = r.get(key)
        if val is None:
            continue
        Post.objects.filter(pk=post_id).update(like_count=int(val))
```

> **Idempotency / correctness.** Writing the absolute Redis value into
> `like_count` is idempotent (not `+= delta`), so a re-run can't double count.
> The Redis counter itself is authoritative-live; Postgres `COUNT(*)` is the
> ultimate backstop if Redis is ever lost (see `like_count()` reseed).
>
> **Hot-key alternative:** if a single celebrity post's INCR rate is extreme,
> shard the counter (`post:{id}:likes:{0..15}`, INCR a random shard, read via
> `MGET`+sum). Start simple; add shards only if you measure contention.

---

## 3.6 "Who liked" + "posts I liked"

```python
# selectors.py
def likers(post_id, cursor):     # paginated user list
    return (Like.objects.filter(post_id=post_id, deleted_at__isnull=True)
            .select_related("user").order_by("-id"))

def liked_post_ids(user_id):
    return (Like.objects.filter(user_id=user_id, deleted_at__isnull=True)
            .values_list("post_id", flat=True))
```

## 3.7 Views / URLs

```python
# POST /api/v1/posts/{id}/like  and  /unlike
class LikeView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, post_id):
        services.like(request.user, post_id); return Response(status=204)
    def delete(self, request, post_id):
        services.unlike(request.user, post_id); return Response(status=204)
```

---

## 3.8 Tests

- `test_like_increments_once_and_is_idempotent`
- `test_unlike_soft_deletes_and_decrements`
- `test_relike_revives_and_counts_once`
- `test_partial_unique_blocks_two_active_likes`
- `test_like_count_reseeds_from_db_on_redis_miss`
- `test_reconciler_writes_absolute_value` (no double count on re-run)
- `test_concurrent_likes_single_active_row` (simulate race)

Use `fakeredis` for Redis in tests.

---

## Definition of done

Likes toggle idempotently, counts are fast (Redis) and durable (reconciled +
reseed-from-COUNT), the partial-unique index enforces one active like, and the
hot-row lock problem is gone.
