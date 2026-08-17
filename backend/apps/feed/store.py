"""Redis feed store — one Sorted Set per user (``feed:{user_id}``).

score = post timestamp, member = post_id. The ZSET gives ordered inserts, range
reads, and cheap trimming in three primitives.
"""
from __future__ import annotations

from django.conf import settings

from apps.common.redis import redis_client

FEED_KEY = "feed:{}"
BUILT_KEY = "feed:{}:built"  # marker so empty feeds aren't rebuilt on every read
_CFG = settings.FEED


def add_to_feeds(post_id: int, score: float, user_ids) -> None:
    """Pipeline ZADD the post into each user's feed, trimming to MAX_LEN."""
    r = redis_client()
    pipe = r.pipeline(transaction=False)
    n = 0
    for uid in user_ids:
        key = FEED_KEY.format(uid)
        pipe.zadd(key, {str(post_id): score})
        pipe.zremrangebyrank(key, 0, -(_CFG["MAX_LEN"] + 1))
        n += 1
        if n % 500 == 0:
            pipe.execute()
    pipe.execute()


def read_entries(user_id, limit: int, max_score: float | None = None) -> list[tuple[int, float]]:
    """Newest-first ``(post_id, score)`` for a user, optionally older than
    ``max_score`` (exclusive) for cursor pagination."""
    r = redis_client()
    hi = "+inf" if max_score is None else f"({max_score}"
    raw = r.zrevrangebyscore(
        FEED_KEY.format(user_id), hi, "-inf", start=0, num=limit, withscores=True
    )
    return [(int(member), score) for member, score in raw]


def feed_present(user_id) -> bool:
    r = redis_client()
    return bool(
        r.exists(FEED_KEY.format(user_id)) or r.exists(BUILT_KEY.format(user_id))
    )


def write_feed(user_id, entries: list[tuple[int, float]]) -> None:
    """Populate a user's feed from a rebuild, always leaving the built-marker."""
    r = redis_client()
    key = FEED_KEY.format(user_id)
    if entries:
        r.zadd(key, {str(pid): score for pid, score in entries})
        r.zremrangebyrank(key, 0, -(_CFG["MAX_LEN"] + 1))
    # Marker so a user who follows nobody isn't rebuilt on every single read.
    r.set(BUILT_KEY.format(user_id), "1", ex=_CFG["EMPTY_MARKER_TTL"])
