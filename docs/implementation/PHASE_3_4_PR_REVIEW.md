# PR Review — Phase 3 (Likes + Counters) + Phase 4 (Outbox + Celery)

Self-review of the Phase 3 and Phase 4 implementation. Reviewed against the phase
specs and repo conventions.

**Verdict:** ✅ Approve. Full suite **54 passed** (was 41; +13 new), `check`
clean, migrations complete. No blocking issues; deferrals are intentional.

---

## What was implemented

**Phase 3 — interactions / likes**
- `apps/common/redis.py` — shared, lazily-built, pooled Redis client with a
  `set_client()` seam for tests.
- `apps/interactions/` — `Like` model with a **partial unique index**
  (`uniq_active_like WHERE deleted_at IS NULL`); services `like`/`unlike`
  (soft-delete + revive), `like_count` (Redis with `COUNT(*)` reseed on miss),
  `reconcile_like_counts` (absolute-value flush to `posts.like_count`); selectors
  (`likers`, `liked_post_ids`); `LikeView`; admin; migration.

**Phase 4 — transactional outbox + Celery**
- `apps/common/models.py::Outbox` (+ migration) — written **in the same
  transaction** as the post.
- `create_post` emits `post.created` into the outbox atomically.
- `apps/common/relay.py` — `register()` + `drain_once()` (SKIP LOCKED on
  Postgres, plain read on SQLite); `run_relay` management command.
- `config/celery.py` + `config/__init__.py` — Celery app, autodiscovery, settings
  (`acks_late`, `reject_on_worker_lost`, prefetch=1, eager toggle).
- `apps/posts/tasks.py` — `on_post_created` (Phase-5 fan-out hook) and
  `process_media_task`; media finalize now enqueues the task instead of running
  inline.

---

## Findings

### 🟢 Verified good
- **No hot-row lock:** likes are independent row inserts; the count is a Redis
  `INCR`/`DECR`. Reads never hit the DB (Redis, with reseed fallback).
- **Idempotent like/unlike + revive:** the partial unique index guarantees one
  *active* like; re-like revives the same row (no accumulation). Covered by
  `test_like_increments_once_and_is_idempotent`, `test_relike_revives_single_row`,
  `test_partial_unique_blocks_two_active_likes`.
- **Count survives Redis loss:** `like_count` reseeds from `COUNT(*)` and
  `reconcile_like_counts` writes the **absolute** value (idempotent re-run).
  Covered by `test_like_count_reseeds_from_db_on_redis_miss`,
  `test_reconcile_writes_absolute_value`.
- **Visibility on like:** `like` calls `get_post`, so you can't like a post you
  can't see / a deleted post (`test_cannot_like_missing_post`).
- **Outbox atomicity:** the outbox row commits with the post or not at all —
  `test_outbox_rolls_back_with_post_on_error` simulates a mid-create failure and
  asserts neither persists.
- **At-least-once + idempotent delivery:** relay dispatches then marks processed;
  a second drain re-dispatches nothing; unregistered events stay pending rather
  than being silently dropped. Covered by the three `test_relay_*` cases.

### 🟡 Minor / accepted
- **Feed still shows the durable `like_count` mirror**, not the live Redis count.
  The like/unlike endpoints return the live count for immediate client feedback;
  the feed reflects Redis after `reconcile_like_counts` runs. Batched live-count
  hydration is **Phase 6** (deliberately not pulled forward, to avoid a
  posts→interactions coupling before the hydration layer exists).
- **`reconcile_like_counts` uses `SCAN`** — fine at moderate scale; at very
  large key counts, drive it from a set of "dirty" post ids instead. Noted for
  later.
- **`process_media_task` runs eagerly** in dev/test (no broker). In production
  run a real worker; `acks_late` handles worker-crash redelivery.

### ⚪ Deferred (by design)
- Fan-out on `post.created` is a **no-op hook** (`on_post_created`) until Phase 5.
- Counter **sharding** / write-behind for a single viral post (design captured in
  the Phase 3 doc discussion) — add only when a metric demands it.
- Celery **beat** schedule for `reconcile_like_counts` — wire when deploying.

---

## Test coverage added (13)

```
apps/interactions/tests/test_likes.py   8
apps/posts/tests/test_outbox.py         2
apps/common/tests/test_relay.py         3
```

Redis is faked (`fakeredis`); Celery runs eager. No external services needed.

---

## Follow-ups before production
1. Run a real Celery worker + `python manage.py run_relay` (don't rely on eager).
2. Schedule `reconcile_like_counts` on Celery beat (e.g. every 60s).
3. Point `REDIS_URL` / `CELERY_BROKER_URL` at real instances; separate DBs.
4. Phase 6 hydration to surface live counts in feed/detail reads.
