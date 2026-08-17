# PR Review — Phase 5 (Feed Fan-out) + Phase 6 (Hydration & Cache)

Detailed self-review of the Phase 5 + 6 implementation. Reviewed against the
phase specs, the design Q&A, and repo conventions.

**Verdict:** ✅ Approve. Full suite **69 passed** (was 54; +15 net), `check`
clean, migrations complete (no new tables — `feed` is a pure Redis/logic app).
Two correctness risks were checked closely (private-post leakage across all three
feed paths; authorization vs. the shared post cache) and are handled. Deferrals
are intentional and listed.

---

## What was implemented

### Phase 5 — `apps/feed/`
- **`store.py`** — Redis ZSET per user (`feed:{id}`): `add_to_feeds` (pipelined
  ZADD + trim to `MAX_LEN`), `read_entries` (ZREVRANGEBYSCORE, cursor-friendly),
  `feed_present` (+ a `:built` marker so empty feeds aren't rebuilt every read),
  `write_feed`.
- **`tasks.py`** — `fanout_post` (skip PRIVATE, skip celebrity authors, batched
  fan-out to followers, idempotent) and `backfill_follow_task`.
- **`selectors.py`** — `home_feed_entries`: hybrid merge of the precomputed ZSET
  with a **live query of only the celebrities the viewer follows**, de-duped and
  time-ordered; rebuild-on-miss.
- **`rebuild.py`** — `rebuild_feed` (from the source-of-truth query) and
  `backfill_follow` (recent posts on new follow; skips celebrities).
- **Wiring** — `fanout_post` registered as the `post.created` outbox handler
  (replacing the Phase 4 placeholder); a `follows.signals.user_followed` signal
  drives backfill **after commit** (so a rolled-back follow can't trigger it),
  keeping `follows` ignorant of `feed`.
- **`views.py` / `urls.py`** — `GET /api/v1/feed/` (moved out of `posts`), with a
  score-based `cursor`.

### Phase 6 — hydration & cache
- **`apps/common/cache.py`** — `single_flight` (stampede protection: one rebuild
  behind a lock, others wait then serve).
- **`apps/posts/services/hydrate.py`** — `hydrate_posts` (batched cache-aside:
  one MGET + one DB query for misses, jittered TTL, negative-cache for deleted),
  `hydrate_single` (single-flight, for post detail), `evict_post`. Posts cached
  **once** by id; author embedded as a snapshot; counts merged live (never
  cached in the blob).
- **`interactions.services.like_counts_bulk`** — batched counts (one MGET + one
  grouped reseed query).
- **Wiring** — `PostDetailView` now serves from the cache with live counts;
  `delete_post` evicts the blob; the feed view hydrates its ids.

---

## Correctness review (the risky bits)

### 🔴→🟢 Private-post leakage — checked on all three delivery paths
A post can reach a feed via three routes; **all** exclude `PRIVATE`:
- **fan-out on write** — `fanout_post` returns early for `visibility == PRIVATE`
  (`test_private_post_not_fanned_out`).
- **celebrity live merge** — `_celebrity_entries` applies
  `.exclude(visibility=PRIVATE)`.
- **rebuild-on-miss** — uses `posts.selectors.home_feed`, which already excludes
  PRIVATE (`test_home_feed_rebuilds_on_missing_key` + the Phase 1 exclusion
  test). `FOLLOWERS` posts are correctly delivered (the viewer follows the
  author).

### 🟢 Authorization vs. the shared post cache
`post:{id}` blobs are shared across all viewers, but **authorization is not**
done at the cache. The feed only ever hydrates ids from the viewer's own feed
(their ZSET + celebrities they follow), and `PostDetailView` gates on
`get_post` (visibility) **before** hydrating. So a cached blob is never returned
to a viewer who shouldn't see it. Verified via
`test_followers_only_post_hidden_from_stranger` (still passes against the hydrated
detail path).

### 🟢 Counts never staleness-trapped in the blob
`_to_blob` omits counts; `_set_blob` caches only the stable part; `_attach_counts`
merges live Redis counts on every render. A like never invalidates a post blob
(`test_hydrate_attaches_live_like_count`, `test_hydrate_second_call_served_from_cache`).

### 🟢 Idempotency & delivery
`fanout_post` is idempotent (ZADD re-set); the outbox→relay path delivers it
at-least-once (`test_fanout_via_outbox_relay`). Backfill enqueues via
`transaction.on_commit`, proven end-to-end with
`test_follow_triggers_backfill_via_signal`.

### 🟢 No N+1
`hydrate_posts` = 1 MGET + 1 `WHERE id IN` (+ 1 grouped count reseed);
`select_related(user, user__profile)` + `prefetch_related(media)`. A warm cache
render is ≤ 1 query (`test_hydrate_second_call_served_from_cache`); the full feed
endpoint is asserted `< 15` queries regardless of size
(`test_feed_render_query_count_is_bounded`).

### 🟢 Deleted posts vanish from feeds
Feeds hold ids; a deleted post is evicted from cache and filtered by `alive()` in
hydration, then negative-cached (`test_hydrate_drops_deleted_and_negative_caches`).
Stale ids linger harmlessly in the ZSET until trimmed.

---

## Minor / accepted

- **Inactive-user skip is a stub** (`_is_active` returns `True`). Needs a
  `last_active` timestamp on `User` to realize the write-saving; deferred with a
  clear TODO.
- **Feed cursor** is score(timestamp)-based and simple; fine for infinite scroll.
  Ties at identical timestamps could theoretically page-boundary-skip (mitigated
  by the `-id` secondary order in source queries).
- **`single_flight` busy-waits** (50 ms poll) for the non-leader; acceptable for
  short rebuilds. A pub/sub wake-up is a later optimization.
- **Unfollow doesn't purge** the ex-followee's posts from the ZSET (they age out
  via trim). No privacy issue (they were visible at delivery time).
- **`hydrate` couples `posts → interactions`** via a public service
  (`like_counts_bulk`). Allowed by the conventions (cross-app via services), and
  it's the composition layer where this belongs — no import cycle
  (`interactions` imports `posts.selectors`, not `posts.services.hydrate`).

## Deferred (by design)
- Stale-while-revalidate (serve stale + async refresh) for the very hottest keys
  — `single_flight` + TTL jitter + negative caching are in; SWR is the next tier.
- Author (`user:{id}`) shared cache — currently a snapshot embedded in the blob
  (goes slightly stale on avatar change), which is the documented trade-off.
- Ranking (chronological only), and the inactive-skip activity signal.

---

## Test coverage added (15)

```
apps/feed/tests/test_feed.py        11  (fan-out direct + via relay, celebrity skip,
                                         private skip, hybrid merge, rebuild-on-miss,
                                         backfill + celebrity-skip, signal wiring,
                                         query-count bound, endpoint)
apps/posts/tests/test_hydrate.py     7  (order, cache-hit no-query, deleted/neg-cache,
                                         live counts, single missing, evict,
                                         single_flight builds once)
```
(3 feed tests were relocated from `test_posts.py` into the feed suite.)

Redis is faked; Celery eager. No external services required.

---

## Follow-ups before production
1. Add a `User.last_active` signal and implement `_is_active` to skip idle
   followers during fan-out (big write saving for large accounts).
2. Consider stale-while-revalidate for celebrity post blobs.
3. Run real Celery workers + `run_relay`; schedule feed rebuilds/trims as needed.
4. Metrics: fan-out lag, cache hit rate, feed rebuild frequency.
