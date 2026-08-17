"""Hybrid feed read: precomputed ZSET (normal followees) merged with a live
query of the celebrities the viewer follows.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.follows.selectors import celebrity_followee_ids
from apps.posts.models import Post
from . import rebuild
from .store import feed_present, read_entries

_CFG = settings.FEED


def home_feed_entries(user, limit: int, before_ts: float | None = None):
    """Return newest-first ``(post_id, score)`` entries for the home feed."""
    if not feed_present(user.id):
        rebuild.rebuild_feed(user)

    precomputed = read_entries(user.id, limit, max_score=before_ts)
    live = _celebrity_entries(user.id, limit, before_ts)

    return _merge(precomputed, live, limit)


def _celebrity_entries(user_id, limit, before_ts):
    celeb_ids = celebrity_followee_ids(user_id)
    if not celeb_ids:
        return []
    window = timezone.now() - timedelta(hours=_CFG["CELEB_WINDOW_HOURS"])
    qs = (
        Post.objects.alive()
        .filter(user_id__in=celeb_ids, created_at__gte=window)
        .exclude(visibility=Post.Visibility.PRIVATE)
    )
    if before_ts is not None:
        qs = qs.filter(created_at__lt=datetime.fromtimestamp(before_ts, dt_timezone.utc))
    rows = qs.order_by("-created_at", "-id").values_list("id", "created_at")[:limit]
    return [(pid, created.timestamp()) for pid, created in rows]


def _merge(precomputed, live, limit):
    """Combine two newest-first lists, de-dupe by post_id, keep newest ``limit``."""
    best: dict[int, float] = {}
    for pid, score in [*precomputed, *live]:
        if pid not in best or score > best[pid]:
            best[pid] = score
    ordered = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return ordered[:limit]
