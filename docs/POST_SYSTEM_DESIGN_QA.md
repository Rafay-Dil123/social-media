# Scalable Post System — Design Q&A

A record of the design session: each decision as a **question**, the **answer we
landed on**, and the **trade-offs** we accepted. Read this to understand *why*
the architecture is shaped the way it is. The concrete schemas and flows live in
`POST_SYSTEM_ARCHITECTURE.md`; the build steps live in
`POST_SYSTEM_IMPLEMENTATION_PLAN.md`.

---

## Q1 — What columns and primary key for the `Post` table?

**Decision.** A `posts` table with a `bigint` (auto-increment or Snowflake/ULID)
primary key, `user_id` FK to the author, a real `caption` text column,
`visibility`, a `jsonb extra` column for sparse metadata, and
`created_at` / `updated_at` / `deleted_at`. Media is **not** stored inline —
it moves to a separate `post_media` table (see Q2).

**Trade-offs.**

- **`bigint` PK vs UUID.** `bigint` gives sequential inserts → tight index
  locality and fast writes on a high-volume table; a time-sortable ID
  (Snowflake/ULID) additionally helps feed ordering. Cost: a plain
  auto-increment is **enumerable** (`/posts/1`, `/posts/2` lets scrapers walk the
  table and infer volume) and is a **single-sequence bottleneck** if the DB is
  ever sharded. Mitigation: expose a non-sequential public id (slug/Snowflake)
  even if the internal PK stays `bigint`. This deliberately deviates from the
  repo-wide UUID convention *for high-volume tables only*, trading
  non-enumerability for write locality.
- **`caption` as a real column vs inside JSON.** Real column because it is shown
  on every render and needs full-text search. Rule applied throughout: **things
  you filter / sort / search / always display → real columns; sparse optional
  data → `jsonb`.**
- **Soft delete (`deleted_at`) vs hard delete.** Soft, because hard-deleting
  social content breaks replies, notifications, and feed references. Cost: every
  read must filter `deleted_at IS NULL`, and rows accumulate.

---

## Q2 — Store media inline on the post, or in a separate table?

**Decision.** Separate `post_media` table, one-to-many from `posts`
(`post_id`, `type`, `storage_key`, `position`, dimensions/duration).

**Trade-offs.**

- **Separate table = a join**, which we accepted. But on the hot path we avoid
  the join two ways: (1) **batch-load** media with one
  `WHERE post_id IN (...)` instead of joining (a join multiplies rows — a
  3-image post returns 3 rows); (2) **denormalize a thumbnail/preview** onto the
  post so feed rendering needs *zero* media lookups, hitting `post_media` only
  when a post is opened.
- **Why not inline?** A single `type`+`url` column can't represent **text-only**
  posts or **carousels** (multiple media). The separate table gives both, plus
  per-media metadata, at the cost of one extra table.
- **Store `storage_key`, not a full URL.** Baking the CDN/bucket domain into
  every row means a bucket/CDN change rewrites millions of rows. Store the key,
  build the URL at read time.

---

## Q3 — Denormalized counters (like/comment counts) or compute on read?

**Decision.** Denormalized counters, but **not** as a naive column incremented
in place. The source of truth is an append-only `likes` table; the live count
lives in **Redis**; a durable counter column is reconciled from Redis
periodically.

**Trade-offs.**

- **Compute-on-read (`COUNT(*)`)** is always correct but far too expensive for a
  hot post read thousands of times per second.
- **Naive denormalized column** (`UPDATE posts SET like_count = like_count + 1`)
  creates a **hot-row lock**: thousands of concurrent likes on a viral post all
  serialize on one row. This is *the* reason naive counters don't scale.
- **Options considered for the hot-row problem:**
  - *Sharded counters* — N rows per post (`post_id, shard_id, count`); increment
    a random shard, read `SUM`. Pure-Postgres, spreads contention by N×.
  - *Async batch aggregation* — likes just `INSERT` (no shared-row contention); a
    worker folds deltas into the counter. Eventually consistent, which is fine
    for a like count.
  - *Redis `INCR` + flush* — live counter in memory, flushed to Postgres for
    durability. **Chosen**, because we're already committed to Postgres + Redis
    and it gives the best write throughput.
  - *OLAP merge engines* — ClickHouse `SummingMergeTree` literally ingests rows
    and sums by key in background merges (the "DB that merges and sums" idea).
    Powerful but a separate system; deferred as an optional analytics path.
- **Accepted cost:** the Redis count is eventually consistent and Redis is an
  extra system to run and pay for. Acceptable at the scale where this matters.

---

## Q4 — How is a "like" stored, and how is an "unlike" represented?

**Decision.** An append-only `likes` table (`user_id`, `post_id`, `created_at`,
`deleted_at`) with a **partial unique index** `UNIQUE(user_id, post_id) WHERE
deleted_at IS NULL`. Unlike = soft delete (set `deleted_at`); re-like toggles a
row back / inserts a new one.

**Trade-offs.**

- **`±1 value` column rejected** — to list *who* liked you'd have to sum `±1`
  per pair and filter `> 0`; expensive and awkward, and it complicates
  idempotency.
- **Soft delete vs hard delete.** Soft delete keeps like/unlike history for
  analytics. With a plain `UNIQUE(user_id, post_id)` you must **toggle** the one
  row (`ON CONFLICT DO UPDATE SET deleted_at = NULL`), losing history; with the
  **partial unique index** you can keep every historical row while enforcing only
  one *active* like. We chose the partial index as the "correct, learns the
  Postgres feature" option; the simple toggle is the lighter alternative.
- **Separation of concerns:** Redis holds the *count*; Postgres `likes` holds
  *who* (paginated list). Different heat, different store.

---

## Q5 — How do we build the home feed: fan-out on write or on read?

**Decision.** **Hybrid fan-out** (the Twitter/X model). Normal authors →
fan-out on **write** (push post id into each follower's precomputed feed).
Celebrity authors (followers over a threshold) → fan-out on **read** (their
posts are merged in at query time). A user's home feed = their precomputed list
**merged with** a live query of just the celebrities they follow.

**Trade-offs.**

- **Fan-out on write** breaks for **high-follower** users: one post → millions of
  feed inserts (write amplification / thundering herd), much of it wasted on
  inactive followers.
- **Fan-out on read** breaks under **read volume**: every feed open re-runs an
  expensive scatter-gather across everyone you follow, repeated with no reuse.
- **Why hybrid wins:** the read-time query covers only the *handful* of
  celebrities you follow (the rest are precomputed), so both sides stay small.
- **Accepted cost:** two code paths, a `is_fanout_on_read` flag that flips as
  users cross the follower threshold, and merge logic at read time.

---

## Q6 — Where does the precomputed feed live, and how is it kept bounded?

**Decision.** A **Redis Sorted Set (ZSET)** per user, `feed:{user_id}`, score =
timestamp, member = `post_id`. `ZADD` to fan-out, `ZREVRANGE` to read latest N,
`ZREMRANGEBYRANK` to trim to ~800 entries. The feed is treated as **derived
data**, rebuildable from the follow graph + recent posts.

**Trade-offs.**

- **Store IDs, not content.** The feed holds `post_id`s (pointers); post content
  is hydrated at read. Keeps feeds tiny and makes edits/deletes automatically
  consistent (a deleted post is filtered during hydration). Never copy post
  bodies into a million feeds.
- **Redis-only vs durable Postgres `feed` table.** Because the feed is derived,
  losing it is survivable — on a cache miss we **lazily rebuild** from source.
  A durable `feed` table (row-per-entry, *not* a JSON blob — a JSON array forces
  a read-modify-write of the whole blob on every fan-out) is optional, purely a
  rebuild shortcut.
- **Recovery / new users / new follows** all use the same rebuild path: query
  recent posts of followees + celebrities, populate the ZSET.
- **Accepted cost:** rebuild cost on cache miss; optional AOF persistence if we
  want Redis restarts to replay.

---

## Q7 — How do 20 post IDs become 20 rendered posts (hydration & caching)?

**Decision.** Cache-aside with **batch** reads. Each post is cached **once**,
keyed by `post:{id}`, shared across all feeds. Feed read → `MGET post:{id}…` →
misses fetched with one `WHERE id IN (...)` → backfilled. Counts read live from
Redis counters; author from `user:{id}` (or a snapshot). Hot-key expiry guarded
by **single-flight lock + stale-while-revalidate + TTL jitter**.

**Trade-offs.**

- **Cache once by `post_id`, not per feed.** The cache size is the *working set
  of distinct hot posts*, **not** `users × 20`. A post in 2M feeds is one Redis
  entry. `maxmemory` + LRU eviction keeps only hot posts.
- **Granularity: stable blob vs volatile counts.** Cache the **stable** part
  (`post:{id}`: text, media keys, author id) with a long TTL; read **volatile**
  counts live from counters and merge at render. A like never invalidates the
  post blob; an edit never touches the counter.
- **Author: shared cache vs snapshot.** `user:{id}` cached once and joined in
  (dedup) *or* a tiny author snapshot embedded in the post blob (fast, but goes
  stale on avatar change). Trade-off left open.
- **Cache stampede (thundering herd).** When a hot key expires, thousands of
  reads miss at once and stampede Postgres. Fixed by: **single-flight lock**
  (first request rebuilds, others wait/serve stale — same "coalesce concurrent
  work" idea as the auth refresh-token dedupe), **stale-while-revalidate** (serve
  stale + refresh in background, never a hard miss), and **TTL jitter** (avoid
  synchronized expiry).

---

## Q8 — What in "create a post" is synchronous vs async, and how is the fan-out never lost?

**Decision.** Synchronous, in one Postgres transaction: `INSERT post` +
`INSERT post_media` + an `INSERT` into an **outbox** table. Async workers
(at-least-once, idempotent): fan-out, ML moderation, mention notifications.
The **transactional outbox** guarantees the fan-out job is never lost.

**Trade-offs.**

- **Dual-write problem.** Committing the post to Postgres and *then* enqueuing
  the fan-out to a separate queue is two non-atomic writes; a crash in the gap
  leaves a post that never reaches any feed.
- **Outbox pattern.** The outbox row commits *in the same transaction* as the
  post (all-or-nothing). A relay worker polls the outbox (or uses CDC) and
  publishes to the queue, then marks it processed. A crash just means the row is
  picked up on restart — nothing lost.
- **Two crash points, two guards.** Outbox covers the **producer** gap
  (commit → enqueue). The queue's **at-least-once + ack/retry** covers the
  **consumer** gap (worker dies mid-fan-out). Because delivery is at-least-once,
  jobs must be **idempotent** — fan-out is naturally idempotent (`ZADD` of the
  same member is a no-op re-set).
- **Accepted cost:** an outbox table + a relay process; slightly more moving
  parts for strong delivery guarantees.

---

## Q9 — How does media upload work without bytes flowing through the API?

**Decision.** **Presigned upload.** Client asks the API for an upload slot; the
API validates type, **generates the storage key server-side**, and returns a
**presigned POST** with baked-in conditions (size range, content-type, short
expiry). The client uploads bytes **directly to the bucket**. Finalization uses a
client confirm **plus** an authoritative S3 event + `HEAD` check. Moderation and
thumbnails run in the background.

**Trade-offs.**

- **Server generates the key, not the client** — prevents overwrites and path
  traversal; the server knows the key before any byte is uploaded and stores it
  in `post_media` (state `pending`).
- **Limits enforced by the signed policy, not the client.** Client-side size
  checks are UX-only and bypassable. `content-length-range` and `content-type`
  are signed into the presigned POST and **enforced by S3**, so the server
  enforces limits without touching the bytes. A post-upload `HEAD` verifies the
  *real* size/type (never trust declared values).
- **Don't trust the client to confirm.** Client confirm gives snappy UX; an S3
  event notification (→ queue/webhook) is the authoritative finalize, and a
  reconciliation job deletes orphaned `pending` uploads.
- **Accepted cost:** a multi-step flow (init → upload → finalize) and bucket
  event wiring, in exchange for keeping large bytes entirely off the app servers.

---

## Q10 — When one Postgres isn't enough, how do we scale the database?

**Decision.** Two independent levers applied in order: **read replicas**
(duplicate data, spread reads) first; **partitioning then sharding** (split
data) later. Shard `posts` and `post_media` on **`user_id`** (co-locate a user's
data); shard `likes` on **`post_id`** (the hot count query).

**Trade-offs.**

- **Replication.** Cheap first move for a read-heavy workload (~100:1). Cost:
  **replication lag** → the **read-your-own-writes** problem, fixed by routing a
  user's reads to the primary briefly after they write.
- **Partitioning vs sharding.** Time-based **partitioning** (by month) is great
  for archiving/pruning old data on one machine. Time is a **bad shard key** —
  all new writes hit the newest shard (write hotspot). **Sharding** across
  machines is the last resort (adds cross-shard queries, rebalancing).
- **Shard key choice.**
  - `posts` / `post_media` on **`user_id`** → co-locates a user's posts + media +
    feed on one shard, avoiding **cross-shard joins**. (Sharding on the post's
    own random PK would scatter a user's data — the join problem.)
  - `likes` on **`post_id`** → optimizes the hot "who/how many liked this post"
    query; accepts that "posts I liked" becomes a scatter. Redis counters already
    relieve this table's write pressure.
- **Applied order:** vertical scaling + indexing → caching (Redis) → read
  replicas → partition by time → shard by `user_id` (last, e.g. via Citus).

---

## One-line summary of the whole design

Text-first `posts` with media split into `post_media`; likes as an append-only
table with a partial-unique index and counts offloaded to Redis; a **hybrid
fan-out** feed stored as Redis ZSETs of post IDs, hydrated cache-aside with
stampede protection; writes made crash-safe with a **transactional outbox** and
idempotent async workers; media uploaded **directly to object storage** via
presigned policies; and a scaling ladder of **cache → replicas → partition →
shard-by-user_id**.
