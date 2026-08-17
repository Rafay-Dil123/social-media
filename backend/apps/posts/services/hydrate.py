"""Phase 6 — turn post IDs into rendered posts, cheaply and safely.

Each post is cached **once** as a stable blob keyed ``post:{id}`` (text, media,
author snapshot), shared across every feed. Volatile counts are read live from
the Redis counters and merged at render, so a like never invalidates the blob.

- ``hydrate_posts(ids)`` — batched cache-aside for a feed page (one MGET + one
  DB query for the misses).
- ``hydrate_single(id)`` — a single post behind a single-flight lock (the hot
  post-detail path).
"""
from __future__ import annotations

import json
import random

from django.conf import settings

from apps.common.cache import single_flight
from apps.common.redis import redis_client
from ..models import Post

_CFG = settings.POST_CACHE
_MISSING = "__missing__"


def hydrate_posts(post_ids: list[int], viewer=None) -> list[dict]:
    if not post_ids:
        return []
    r = redis_client()

    cached = r.mget([f"post:{pid}" for pid in post_ids])
    blobs: dict[int, dict] = {}
    misses: list[int] = []
    for pid, raw in zip(post_ids, cached):
        if raw is None:
            misses.append(pid)
        elif raw != _MISSING:
            blobs[pid] = json.loads(raw)

    if misses:
        found = _fetch_and_cache(misses)
        blobs.update(found)
        for pid in misses:
            if pid not in found:
                r.set(f"post:{pid}", _MISSING, ex=_CFG["NEG_TTL"])

    _attach_counts(blobs)
    # Preserve feed order; drop anything missing/deleted.
    return [blobs[pid] for pid in post_ids if pid in blobs]


def hydrate_single(post_id: int) -> dict | None:
    raw = single_flight(
        f"post:{post_id}",
        lambda: _build_value(post_id),
        lock_ttl=_CFG["LOCK_TTL"],
    )
    if raw == _MISSING:
        return None
    blob = json.loads(raw)
    _attach_counts({post_id: blob})
    return blob


# --- internals ------------------------------------------------------------

def _fetch_and_cache(post_ids: list[int]) -> dict[int, dict]:
    r = redis_client()
    rows = (
        Post.objects.alive()
        .select_related("user", "user__profile")
        .prefetch_related("media")
        .filter(id__in=post_ids)
    )
    found: dict[int, dict] = {}
    for post in rows:
        blob = _to_blob(post)
        found[post.id] = blob
        r.set(f"post:{post.id}", json.dumps(blob), ex=_ttl())
    return found


def _build_value(post_id: int) -> tuple[str, int]:
    post = (
        Post.objects.alive()
        .select_related("user", "user__profile")
        .prefetch_related("media")
        .filter(pk=post_id)
        .first()
    )
    if post is None:
        return _MISSING, _CFG["NEG_TTL"]
    return json.dumps(_to_blob(post)), _ttl()


def _attach_counts(blobs: dict[int, dict]) -> None:
    if not blobs:
        return
    from apps.interactions.services import like_counts_bulk

    counts = like_counts_bulk(list(blobs))
    for pid, blob in blobs.items():
        blob["like_count"] = counts.get(pid, 0)
        blob.setdefault("comment_count", 0)


def _to_blob(post: Post) -> dict:
    profile = getattr(post.user, "profile", None)
    return {
        "id": post.id,
        "caption": post.caption,
        "visibility": post.get_visibility_display(),
        "created_at": post.created_at.isoformat(),
        "author": {
            "id": str(post.user_id),
            "username": post.user.username,
            "avatar_url": getattr(profile, "avatar_url", "") or "",
        },
        "media": [
            {
                "id": m.id,
                "type": m.get_type_display(),
                "url": f"{settings.MEDIA_CDN_BASE.rstrip('/')}/{m.storage_key}",
                "position": m.position,
                "width": m.width,
                "height": m.height,
                "duration_ms": m.duration_ms,
            }
            for m in post.media.all()
        ],
    }


def _ttl() -> int:
    j = _CFG["TTL_JITTER"]
    return _CFG["TTL_SECONDS"] + random.randint(-j, j)


def evict_post(post_id: int) -> None:
    redis_client().delete(f"post:{post_id}")
