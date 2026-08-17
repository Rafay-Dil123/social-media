"""Like write/read operations.

The durable row goes to Postgres (separate rows -> no hot-row lock); the live
count is an atomic Redis counter. Reads take the count from Redis and reseed
from ``COUNT(*)`` on a miss, so the count survives Redis loss.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.exceptions import NotFound
from apps.common.redis import redis_client
from apps.posts.selectors import get_post
from .models import Like

LIKES_KEY = "post:{}:likes"


@transaction.atomic
def like(user, post_id: int) -> None:
    """Idempotent: liking an already-liked post is a no-op (count unchanged)."""
    if get_post(user, post_id) is None:
        raise NotFound("Post not found.")

    active = (
        Like.objects.select_for_update()
        .filter(user=user, post_id=post_id, deleted_at__isnull=True)
        .first()
    )
    if active:
        return

    revived = (
        Like.objects.filter(user=user, post_id=post_id).order_by("-id").first()
    )
    if revived is not None and revived.deleted_at is not None:
        revived.deleted_at = None
        revived.save(update_fields=["deleted_at"])
    else:
        try:
            with transaction.atomic():
                Like.objects.create(user=user, post_id=post_id)
        except IntegrityError:
            return  # lost a race; another tx created the active like
    redis_client().incr(LIKES_KEY.format(post_id))


@transaction.atomic
def unlike(user, post_id: int) -> None:
    active = (
        Like.objects.select_for_update()
        .filter(user=user, post_id=post_id, deleted_at__isnull=True)
        .first()
    )
    if not active:
        return
    active.deleted_at = timezone.now()
    active.save(update_fields=["deleted_at"])
    redis_client().decr(LIKES_KEY.format(post_id))


def like_count(post_id: int) -> int:
    r = redis_client()
    key = LIKES_KEY.format(post_id)
    val = r.get(key)
    if val is not None:
        return int(val)
    # Cache miss / Redis lost -> reseed from the source of truth.
    n = Like.objects.filter(post_id=post_id, deleted_at__isnull=True).count()
    r.set(key, n)
    return n


def like_counts_bulk(post_ids: list[int]) -> dict[int, int]:
    """Batched like counts for hydration: one Redis MGET, one grouped DB query
    to reseed the misses."""
    if not post_ids:
        return {}
    from django.db.models import Count
    from .models import Like

    r = redis_client()
    values = r.mget([LIKES_KEY.format(pid) for pid in post_ids])
    out: dict[int, int] = {}
    misses: list[int] = []
    for pid, val in zip(post_ids, values):
        if val is None:
            misses.append(pid)
        else:
            out[pid] = int(val)

    if misses:
        rows = (
            Like.objects.filter(post_id__in=misses, deleted_at__isnull=True)
            .values("post_id")
            .annotate(c=Count("id"))
        )
        counts = {row["post_id"]: row["c"] for row in rows}
        for pid in misses:
            c = counts.get(pid, 0)
            out[pid] = c
            r.set(LIKES_KEY.format(pid), c)
    return out


def reconcile_like_counts() -> int:
    """Flush live Redis counts into the durable ``posts.like_count`` mirror.

    Writes the absolute value (not a delta), so re-running is idempotent. Meant
    to run periodically (Celery beat). Returns the number of posts updated.
    """
    from apps.posts.models import Post

    r = redis_client()
    updated = 0
    for key in r.scan_iter(match="post:*:likes", count=500):
        parts = key.split(":")
        if len(parts) != 3:
            continue
        try:
            post_id = int(parts[1])
        except ValueError:
            continue
        val = r.get(key)
        if val is None:
            continue
        Post.objects.filter(pk=post_id).update(like_count=int(val))
        updated += 1
    return updated
