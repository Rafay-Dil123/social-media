# Scalable Post System — Implementation Plan

How the design in `POST_SYSTEM_ARCHITECTURE.md` gets built, in order, **without
code**. It follows the repo conventions from `ARCHITECTURE.md`: one Django app
per domain under `apps/`, thin views, business logic in `services/` (writes) and
`selectors.py` (reads), per-app tests, everything under `/api/v1/`.

The guiding principle: **build the correct simple version first, prove it with
tests and load, then layer scale**. Each phase is shippable on its own.

---

## Phase 0 — Foundations (prerequisites)

**Goal.** The shared plumbing everything else leans on.

- `apps/common`: confirm `UUIDModel`, `TimeStampedModel`, the error envelope,
  base pagination (add **cursor** pagination — offset pagination breaks on large
  feeds), and `IsOwnerOrReadOnly`.
- `apps/follows`: `Follow(follower, following)` with `unique_together` and a
  self-follow check. The feed can't exist without "who follows whom." Expose
  `selectors.followee_ids(user)` and `selectors.follower_ids(user)`.
- Add a `follower_count` (denormalized) and `is_fanout_on_read` flag to the user
  (or a `follows` summary row), updated when follows change — the feed needs the
  celebrity threshold.

**Deliverable.** Follow/unfollow endpoints + selectors, tested.

---

## Phase 1 — Posts CRUD (synchronous core, no scale yet)

**Goal.** Create/read/delete posts and media, correct and simple. No Redis, no
fan-out — the feed is a plain query. This is the backbone everything hangs on.

- **`apps/posts` models.** `Post` and `PostMedia` exactly as in the architecture
  doc (bigint PK note; `deleted_at` soft delete; `state` on media). Migrations.
- **Serializers.** Split by direction: `PostCreateSerializer` (caption,
  visibility, media upload_ids) vs `PostReadSerializer` (hydrated shape).
- **Services (writes).** `services/posts.py`:
  - `create_post(user, caption, visibility, media_ids)` — one transaction:
    insert post, attach `post_media` rows, (outbox added in Phase 4).
  - `delete_post(user, post_id)` — soft delete, permission-checked.
- **Selectors (reads).** `selectors.py`:
  - `get_post(id)` (author-scoped visibility rules).
  - `list_user_posts(user_id, cursor)`.
  - `naive_home_feed(user, cursor)` — `WHERE user_id IN (followees) ORDER BY
    created_at LIMIT n`. Deliberately the "fan-out on read" version; it's the
    fallback/rebuild query later.
- **Views/urls.** `POST /posts`, `GET /posts/{id}`, `DELETE /posts/{id}`,
  `GET /feed` (naive for now). Thin — parse, call service/selector, return.
- **Tests.** Create/read/delete, visibility, soft-delete filtering, ownership
  permissions, pagination.

**Deliverable.** A working post system on Postgres alone. Ship it. Everything
after this is scaling the same behavior.

---

## Phase 2 — Media upload (presigned, direct-to-bucket)

**Goal.** Large files never flow through the API.

- **`apps/media`** (or a submodule of `posts`).
- **Init endpoint.** `POST /media/upload-init {content_type, size}`:
  validate type against a whitelist; generate `storage_key`
  (`uploads/{user_id}/{uuid}.{ext}`); create `post_media(state=pending)`; build a
  **presigned POST** with `content-length-range`, `content-type`, short expiry
  (via `boto3` / storage SDK); return `{upload_id, url, fields}`.
- **Finalize.** Bucket event notification (SNS/SQS/webhook) → verify with `HEAD`
  (real size/type) → enqueue moderation/thumbnail job → `state=ready`. Also a
  lightweight client-confirm endpoint for snappy UX.
- **Orphan cleanup.** Scheduled job deletes `pending` uploads past a TTL.
- **Wire into `create_post`.** Only `ready` media may attach.
- **Tests.** Type whitelist rejection; presigned policy fields; finalize flips
  state; orphan cleanup; create_post rejects non-ready media. Mock the bucket.

**Deliverable.** Posts with real image/video uploads, bytes off the app servers.

---

## Phase 3 — Interactions & counters (Redis)

**Goal.** Likes/comments with counts that survive hot posts.

- **`apps/interactions` (or `likes`).** `Like` model with the **partial unique
  index** (`WHERE deleted_at IS NULL`). Comments can follow the same shape.
- **Services.** `toggle_like(user, post_id)` — upsert/soft-delete the row, then
  `INCR`/`DECR post:{id}:likes` in Redis. Idempotent (re-like is a no-op).
- **Counter read.** Hydration reads counts from Redis; on a Redis miss, reseed
  from `COUNT(*)` over active likes and set the key.
- **Reconciler.** A scheduled job flushes `post:{id}:likes` → `posts.like_count`
  (idempotent; use atomic read-reset of a delta or set-from-authoritative).
- **"Who liked".** Paginated selector over active likes.
- **Tests.** Toggle idempotency; partial-unique enforcement; Redis INCR/DECR;
  reseed-from-COUNT on cache loss; reconciler correctness.

**Deliverable.** Likes that don't hot-row-lock; counts fast and durable.

---

## Phase 4 — Crash-safe writes (transactional outbox)

**Goal.** A created post's fan-out can never be lost.

- **`outbox` table** in `common` (shared). `create_post` now also inserts an
  `outbox` row **in the same transaction**.
- **Relay process.** Standalone worker (management command or Celery beat):
  poll `outbox WHERE processed_at IS NULL` (or Postgres `LISTEN/NOTIFY`, or CDC
  later), publish the fan-out job to the queue, mark processed. At-least-once.
- **Queue + worker infra.** Introduce Celery (or RQ) + broker (Redis/RabbitMQ).
  Configure **ack-late** + retries so a worker crash redelivers.
- **Tests.** Outbox row committed atomically with the post (simulate crash after
  commit → relay still delivers); relay idempotency; duplicate delivery is safe.

**Deliverable.** Reliable async pipeline. No user-visible change yet — it's the
safety net for Phase 5.

---

## Phase 5 — Feed fan-out (Redis ZSET, hybrid)

**Goal.** Replace the naive feed query with precomputed feeds + celebrity merge.

- **Fan-out worker** (consumes `post.created`):
  - Normal author → load follower ids in batches → pipelined
    `ZADD feed:{follower}` → `ZREMRANGEBYRANK` cap → skip inactive followers.
  - Celebrity author (`is_fanout_on_read`) → skip.
- **Feed read (`selectors.home_feed`)** becomes hybrid:
  - `ZREVRANGE feed:{user}` for precomputed ids.
  - Live query for celebrities the user follows.
  - Merge, sort by time, de-dupe.
- **Rebuild-on-miss.** If `feed:{user}` is absent (Redis loss / new user / new
  follow) → rebuild via the Phase 1 `naive_home_feed` query → populate ZSET.
- **New-follow backfill.** On follow, inject the followee's recent posts into the
  follower's ZSET (or lazily on next read).
- **Tests.** Fan-out writes to follower feeds; celebrity skip + read-merge;
  cap/trim; rebuild-on-miss; backfill on new follow; ordering correctness.

**Deliverable.** Feeds that scale to high-follower authors and heavy reads.

---

## Phase 6 — Hydration & cache (cache-aside + stampede protection)

**Goal.** Turn feed ids into rendered posts cheaply, safely under load.

- **Post cache.** `services/hydrate.py`:
  - Batch `MGET post:{id}…`; fetch misses with one `WHERE id IN (...)`; backfill
    with **jittered TTL**. Cache the **stable blob only** (no counts inside).
  - Author via `user:{id}` cache (or embed a snapshot).
  - Merge in live counts from Redis at render.
- **Stampede protection.** Single-flight lock (`SET lock:post:{id} NX EX`) around
  rebuilds; stale-while-revalidate; TTL jitter.
- **Cache invalidation.** On post edit/delete → drop/refresh `post:{id}`. On
  author change → drop `user:{id}`.
- **Tests.** Batch hydration; miss→backfill; blob excludes volatile counts;
  single-flight (concurrent misses → one DB hit); stale-serve path.

**Deliverable.** Read path is fast and stampede-proof.

---

## Phase 7 — Database scaling (as needed, not upfront)

**Goal.** Grow past one Postgres — only when metrics demand it.

- **Indexing/query pass** first (explain-analyze the feed + like queries).
- **Read replicas.** Add a replica; route reads via a DB router; handle
  **read-your-own-writes** (pin a user to primary for a few seconds post-write).
- **Partition by time.** Convert `posts` (and `likes`) to monthly partitions;
  add a job to create future partitions and prune/archive old ones.
- **Shard (last).** If needed: shard `posts`/`post_media` by **`user_id`**,
  `likes` by **`post_id`** — via **Citus** or app-level routing. Plan the
  cross-shard cases (global search, "posts I liked") explicitly.
- **Tests/verification.** Replica lag handling; partition pruning in query plans;
  shard-key routing; a load test proving the target (e.g. hot post at 5k
  likes/sec, feed reads at target QPS).

**Deliverable.** A system that scales horizontally with a measured ceiling.

---

## Cross-cutting (throughout, not a phase)

- **Observability.** Structured logs, request/worker metrics (queue depth,
  cache hit rate, replication lag, reconciler drift), tracing on the read path.
- **Rate limiting / abuse.** Throttle post/like creation; dedupe rapid double
  submits (same coalescing idea as the auth refresh dedupe).
- **Idempotency keys** on `POST /posts` so a client retry can't double-create.
- **Feature flags** for fan-out mode and cache paths, so each phase can be rolled
  out and rolled back safely.
- **Load testing** (k6/Locust) gating Phases 5–7 — you can't tune fan-out or
  caching without a synthetic hot post and a follower fan-out storm.

---

## Build order at a glance

```
0 common + follows        → graph + shared plumbing
1 posts CRUD (Postgres)   → correct, shippable core          ← ship
2 media (presigned)       → real uploads, bytes off API
3 interactions + counters → likes, Redis counts
4 outbox + queue          → crash-safe async pipeline
5 feed fan-out (ZSET)     → hybrid precomputed feeds
6 hydration + cache       → fast, stampede-proof reads
7 DB scaling              → replicas → partition → shard (as needed)
```

Phases 1 and 2 give a usable product; 3–6 make it scale; 7 is applied only when
load data says so. Each phase ends with tests, and 5–7 additionally end with a
load test.
