# Phase 6 — Hydration & cache (cache-aside + stampede guard)

**Goal.** Turn a list of post IDs into fully rendered posts cheaply and safely
under load. Cache each post **once** (shared across all feeds), read volatile
counts live, and survive hot-key expiry without stampeding Postgres.

**New:** `apps/posts/services/hydrate.py`, `apps/common/cache.py`.

---

## 6.1 Settings

```python
POST_CACHE = {
    "TTL_SECONDS": 600,          # base TTL for the stable blob
    "TTL_JITTER": 120,           # ± jitter to avoid synchronized expiry
    "LOCK_TTL": 5,               # single-flight rebuild lock
    "NEG_TTL": 30,               # cache "deleted/missing" briefly
}
```

Redis `maxmemory` + `allkeys-lru` (deploy config) so the cache holds the hot
working set and evicts cold posts automatically.

## 6.2 Cache key discipline

| key | holds | TTL | changes when |
|-----|-------|-----|--------------|
| `post:{id}` | **stable** blob: caption, media keys, author_id, created_at | long (jittered) | post edited/deleted |
| `user:{id}` | author snapshot: username, avatar | long | profile edited |
| `post:{id}:likes` / `:comments` | volatile counts (Phase 3) | live | every like/comment |

Counts are **never** baked into `post:{id}` — otherwise every like invalidates
the blob.

## 6.3 The hydration service

```python
# apps/posts/services/hydrate.py
from __future__ import annotations

import json, random
from django.conf import settings

from apps.common.redis import redis_client
from apps.posts.models import Post
from apps.interactions.services import LIKES_KEY   # + comments key

_CFG = settings.POST_CACHE


def hydrate_posts(post_ids: list[int], viewer=None) -> list[dict]:
    if not post_ids:
        return []
    r = redis_client()

    # 1) Batch-get stable blobs.
    blob_keys = [f"post:{pid}" for pid in post_ids]
    cached = r.mget(blob_keys)
    posts, misses = {}, []
    for pid, raw in zip(post_ids, cached):
        if raw is None:
            misses.append(pid)
        elif raw != "__MISSING__":
            posts[pid] = json.loads(raw)

    # 2) One DB query for misses; backfill (single-flight for hot keys).
    if misses:
        rows = (Post.objects.alive()
                .select_related("user", "user__profile")
                .prefetch_related("media")
                .filter(id__in=misses))
        found = {}
        for p in rows:
            blob = _to_blob(p)
            found[p.id] = blob
            _set_blob(r, p.id, blob)
        # Negative-cache the truly missing (deleted) ids to stop repeat DB hits.
        for pid in misses:
            if pid not in found:
                r.set(f"post:{pid}", "__MISSING__", ex=_CFG["NEG_TTL"])
        posts.update(found)

    # 3) Attach live counts + author, in batch.
    _attach_counts(r, posts)
    _attach_authors(r, posts)

    # 4) Return in feed order, dropping deleted.
    return [posts[pid] for pid in post_ids if pid in posts]


def _set_blob(r, pid, blob):
    ttl = _CFG["TTL_SECONDS"] + random.randint(-_CFG["TTL_JITTER"], _CFG["TTL_JITTER"])
    r.set(f"post:{pid}", json.dumps(blob), ex=ttl)


def _attach_counts(r, posts):
    if not posts:
        return
    ids = list(posts)
    likes = r.mget([LIKES_KEY.format(i) for i in ids])
    for pid, lc in zip(ids, likes):
        posts[pid]["like_count"] = int(lc) if lc is not None else _reseed_likes(pid)
    # (comments analogous)


def _to_blob(p) -> dict:
    return {
        "id": p.id, "caption": p.caption, "author_id": str(p.user_id),
        "created_at": p.created_at.isoformat(),
        "media": [{"type": m.type, "key": m.storage_key, "position": m.position,
                   "w": m.width, "h": m.height} for m in p.media.all()],
    }
```

> Media URLs are built from `key` at serialization time (as in Phase 1's
> `MediaReadSerializer.get_url`), so a CDN change never touches the cache.

## 6.4 Stampede protection (hot-key expiry)

When Ronaldo's `post:{id}` expires, thousands of feed reads miss at once. Guard
the rebuild so only **one** request hits Postgres:

```python
# apps/common/cache.py
import time
from apps.common.redis import redis_client

def single_flight(key: str, ttl: int, rebuild):
    """Return cached value; if missing, exactly one caller rebuilds."""
    r = redis_client()
    val = r.get(key)
    if val is not None:
        return val
    lock = f"lock:{key}"
    if r.set(lock, "1", nx=True, ex=ttl):        # I won the rebuild
        try:
            value = rebuild()
            return value
        finally:
            r.delete(lock)
    # Someone else is rebuilding: brief wait, then serve whatever's there.
    for _ in range(ttl * 10):
        time.sleep(0.1)
        val = r.get(key)
        if val is not None:
            return val
    return rebuild()   # fallback (rare)
```

Two complementary techniques already applied above:

- **TTL jitter** (`_set_blob`) — desynchronizes mass expiry so a whole feed's
  worth of posts don't all miss on the same tick.
- **Negative caching** (`__MISSING__`) — a deleted/hot-missing post doesn't
  hammer the DB on every read.

**Stale-while-revalidate (optional upgrade).** Store the blob with a soft-expiry
field; past soft-expiry serve the stale blob and enqueue a background refresh
instead of blocking. Best UX for the very hottest keys.

## 6.5 Invalidation

```python
def evict_post(post_id): redis_client().delete(f"post:{post_id}")
def evict_user(user_id): redis_client().delete(f"user:{user_id}")
```

Call `evict_post` from `posts.services.delete_post` and any edit path; `evict_user`
from profile updates. Counts need no eviction — they live in their own keys.

## 6.6 Tests

- `test_hydrate_batches_mget_then_single_db_query_for_misses`
- `test_blob_excludes_volatile_counts`
- `test_counts_attached_live_and_reseed_on_miss`
- `test_deleted_post_negative_cached_and_dropped_from_results`
- `test_single_flight_one_db_hit_under_concurrent_miss` (spawn threads / fakeredis)
- `test_ttl_has_jitter`
- `test_evict_post_removes_blob`

Assert the **DB query count** during hydration is bounded (miss path = 1 query
regardless of page size).

---

## Definition of done

A feed page of N ids renders with ~1 `MGET` + at most 1 batched DB query for
misses; posts are cached once and shared; counts stay live; and a hot post's
cache expiry triggers a single rebuild, not a thundering herd.
