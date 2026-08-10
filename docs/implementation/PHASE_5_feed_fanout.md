# Phase 5 — Feed fan-out (hybrid, Redis ZSET)

**Goal.** Replace the naive feed query with precomputed per-user feeds
(fan-out on write) for normal authors, plus a read-time merge for celebrities.

**New app:** `apps.feed`. Consumes the `post.created` job from Phase 4.

---

## 5.1 Settings

```python
FEED = {
    "MAX_LEN": 800,                 # entries kept per user ZSET
    "READ_PAGE": 20,
    "CELEB_WINDOW_HOURS": 48,       # how far back to pull celeb posts at read
    "FANOUT_BATCH": 5000,           # followers per ZADD pipeline batch
    "INACTIVE_DAYS": 30,            # skip fan-out to users idle longer than this
}
```

## 5.2 Feed store helpers — `apps/feed/store.py`

```python
from __future__ import annotations

from django.conf import settings
from apps.common.redis import redis_client

FEED_KEY = "feed:{}"
_CFG = settings.FEED


def add_to_feeds(post_id: int, ts: float, user_ids) -> None:
    r = redis_client()
    pipe = r.pipeline(transaction=False)
    for i, uid in enumerate(user_ids, 1):
        key = FEED_KEY.format(uid)
        pipe.zadd(key, {post_id: ts})
        pipe.zremrangebyrank(key, 0, -(_CFG["MAX_LEN"] + 1))   # trim oldest
        if i % 1000 == 0:
            pipe.execute()
    pipe.execute()


def read_feed_ids(user_id, limit) -> list[int]:
    r = redis_client()
    raw = r.zrevrange(FEED_KEY.format(user_id), 0, limit - 1)
    return [int(x) for x in raw]


def feed_exists(user_id) -> bool:
    return redis_client().exists(FEED_KEY.format(user_id)) == 1
```

## 5.3 Fan-out task — `apps/feed/tasks.py`

```python
from __future__ import annotations

from celery import shared_task

from apps.accounts.models import User
from apps.follows.selectors import follower_ids
from apps.posts.models import Post
from .store import add_to_feeds


@shared_task(acks_late=True, max_retries=5)
def fanout_post(post_id: int, author_id: str):
    post = Post.objects.filter(pk=post_id, deleted_at__isnull=True).first()
    if post is None:
        return
    author = User.objects.get(pk=author_id)

    # Celebrities are pulled at read time — never fanned out.
    if author.is_fanout_on_read:
        return

    ts = post.created_at.timestamp()
    batch = []
    for uid in follower_ids(author_id):          # chunked iterator, not a list
        if _is_active(uid):                      # skip long-idle users
            batch.append(uid)
        if len(batch) >= 5000:
            add_to_feeds(post_id, ts, batch); batch.clear()
    if batch:
        add_to_feeds(post_id, ts, batch)
```

Register it with the relay dispatch table (Phase 4):
`relay.register("post.created", fanout_post)`.

> **Idempotency.** `ZADD` of the same `(post_id, ts)` is a no-op re-set, so a
> redelivered fan-out job is harmless. The trim is also idempotent.
>
> **Inactive skip.** Don't fan out to users idle > `INACTIVE_DAYS`; their feed is
> rebuilt on next login (5.5). Massive write savings on huge follower sets.

## 5.4 Hybrid read — `apps/feed/selectors.py`

```python
from __future__ import annotations

from datetime import timedelta
from django.utils import timezone

from apps.follows.selectors import celebrity_followee_ids
from apps.posts.models import Post
from .store import read_feed_ids, feed_exists
from . import rebuild


def home_feed_ids(user, limit) -> list[int]:
    if not feed_exists(user.id):
        rebuild.rebuild_feed(user)               # lazy regeneration (5.5)

    precomputed = read_feed_ids(user.id, limit)  # from normal followees

    celeb_ids = celebrity_followee_ids(user.id)  # small set
    live = []
    if celeb_ids:
        window = timezone.now() - timedelta(hours=48)
        live = list(
            Post.objects.alive()
            .filter(user_id__in=celeb_ids, created_at__gte=window)
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)[:limit]
        )

    merged = _merge_by_time(precomputed, live)   # dedupe, newest first
    return merged[:limit]
```

`_merge_by_time` sorts the union of ids by their post timestamp (fetch timestamps
in one `WHERE id IN` if not already known) and de-dupes.

Then hydration (Phase 6) turns these ids into rendered posts.

## 5.5 Rebuild-on-miss + backfill — `apps/feed/rebuild.py`

Same query as Phase 1's `naive_home_feed`, written into the ZSET.

```python
def rebuild_feed(user) -> None:
    from apps.posts.selectors import naive_home_feed
    from .store import add_to_feeds

    rows = list(naive_home_feed(user).values_list("id", "created_at")[:800])
    if not rows:
        # touch the key so we don't rebuild on every read for a user who
        # follows no one (store a sentinel / short-TTL empty marker)
        return
    # group by ts and ZADD (one pipeline)
    for pid, created in rows:
        add_to_feeds(pid, created.timestamp(), [user.id])


def backfill_on_follow(follower_id, followee_id) -> None:
    """When A follows B, inject B's recent posts into A's feed."""
    from apps.posts.models import Post
    from .store import add_to_feeds
    recent = Post.objects.alive().filter(user_id=followee_id).order_by("-created_at")[:50]
    for p in recent:
        add_to_feeds(p.id, p.created_at.timestamp(), [follower_id])
```

Call `backfill_on_follow` from `follows.services.follow` (or enqueue it).

## 5.6 View

```python
class HomeFeedView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        ids = feed_selectors.home_feed_ids(request.user, settings.FEED["READ_PAGE"])
        posts = hydrate.hydrate_posts(ids, viewer=request.user)   # Phase 6
        return Response({"results": posts})
```

Cursor pagination over a ZSET uses the **score (timestamp)** as the cursor:
`ZREVRANGEBYSCORE feed:{u} (last_ts -inf LIMIT 0 20`.

## 5.7 Tests

- `test_fanout_writes_post_into_follower_feeds`
- `test_celebrity_author_is_not_fanned_out`
- `test_feed_trimmed_to_max_len`
- `test_home_feed_merges_precomputed_and_celebrity_posts_in_time_order`
- `test_rebuild_on_missing_feed_key`
- `test_backfill_on_new_follow`
- `test_fanout_idempotent_on_redelivery`
- `test_inactive_users_skipped`

Use `fakeredis`; assert ZSET contents directly.

---

## Definition of done

Normal authors fan out to follower ZSETs (batched, capped, skipping idle users);
celebrities are merged at read; feeds rebuild lazily on miss and backfill on new
follow; the read path returns a correctly time-ordered id list ready for
hydration.
