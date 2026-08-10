# Scalable Post System — Architecture

The concrete design for the post system: data model, Redis keys, the read and
write paths, and the scaling ladder. This complements the top-level
`ARCHITECTURE.md` (app layout / conventions) and follows the same conventions:
thin views, business logic in `services/` (writes) and `selectors.py` (reads),
domain isolation, everything under `/api/v1/`.

The reasoning behind each choice is in `POST_SYSTEM_DESIGN_QA.md`.

---

## Components at a glance

```
                         ┌─────────────┐
  client ── presigned ──▶│ Object store│  (S3/GCS)  bytes never touch the API
     │      upload       └─────────────┘
     │
     │  POST /posts (sync)      ┌───────────┐        ┌──────────────┐
     └─────────────────────────▶│  Postgres │◀──────▶│   replicas   │ (reads)
                                │  (truth)  │        └──────────────┘
                                │ posts,    │
                                │ post_media│──outbox──▶ relay ──▶ queue ──▶ workers
                                │ likes,    │                                 │
                                │ outbox    │                    fan-out / moderation /
                                └───────────┘                    notifications
                                      ▲
                        counts /      │  cache-aside (hydrate)
                        feed ZSET     ▼
                                ┌───────────┐
                                │   Redis   │  feed:{u}, post:{id}, user:{id},
                                │  (cache)  │  post:{id}:likes / :comments
                                └───────────┘
```

- **Postgres** — durable source of truth (posts, media, likes, outbox).
- **Redis** — feed lists (ZSET), post/author cache, live counters.
- **Object store** — media bytes (S3/GCS), fronted by a CDN.
- **Queue + workers** — async fan-out, moderation, notifications.
- **Relay** — moves outbox rows to the queue (crash-safe hand-off).

---

## Data model (Postgres)

### `posts`

| column | type | notes |
|--------|------|-------|
| `id` | `bigint` PK | sequential/ Snowflake; expose a non-sequential public id |
| `user_id` | FK → `users` | author; index `(user_id, created_at DESC)` |
| `caption` | `text` | real column (text-only posts allowed); FTS target |
| `visibility` | `smallint`/enum | `public` \| `followers` \| `private` |
| `media_preview` | `jsonb` null | denormalized first-thumbnail, avoids media join on feed |
| `extra` | `jsonb` | sparse optional metadata (alt text, camera, filter…) |
| `like_count` | `bigint` default 0 | durable mirror, reconciled from Redis |
| `comment_count` | `bigint` default 0 | same |
| `created_at` | `timestamptz` | index; feed ordering |
| `updated_at` | `timestamptz` | |
| `deleted_at` | `timestamptz` null | soft delete; all reads filter `IS NULL` |

Indexes: `(user_id, created_at DESC)` for a user's posts; partial
`WHERE deleted_at IS NULL`; GIN on `to_tsvector(caption)` for search (later).

### `post_media`

| column | type | notes |
|--------|------|-------|
| `id` | `bigint` PK | |
| `post_id` | FK → `posts` | index |
| `type` | `smallint`/enum | `image` \| `video` |
| `storage_key` | `text` | bucket path, **not** a full URL |
| `position` | `smallint` | ordering within a carousel |
| `width` / `height` / `duration_ms` | ints null | render without downloading |
| `state` | `smallint`/enum | `pending` \| `ready` \| `failed` (moderation) |

### `likes`

| column | type | notes |
|--------|------|-------|
| `id` | `bigint` PK | |
| `user_id` | FK → `users` | |
| `post_id` | FK → `posts` | |
| `created_at` | `timestamptz` | |
| `deleted_at` | `timestamptz` null | unlike = soft delete |

Constraint: **partial unique index** — one *active* like per user per post:

```sql
CREATE UNIQUE INDEX uniq_active_like
  ON likes (user_id, post_id) WHERE deleted_at IS NULL;
```

Read patterns: count → Redis; "who liked" →
`SELECT user_id FROM likes WHERE post_id=? AND deleted_at IS NULL` (paginated).

### `outbox`

| column | type | notes |
|--------|------|-------|
| `id` | `bigint` PK | |
| `event_type` | `text` | e.g. `post.created` |
| `payload` | `jsonb` | `{post_id, author_id, ...}` |
| `created_at` | `timestamptz` | |
| `processed_at` | `timestamptz` null | relay marks it done |

Written **in the same transaction** as the post. Relay index: `WHERE
processed_at IS NULL`.

### `feed` (optional, durable rebuild shortcut)

Row-per-entry (`user_id`, `post_id`, `created_at`), index
`(user_id, created_at DESC)`. **Never** a JSON array of ids. Optional — the
authoritative feed lives in Redis and is rebuildable from source.

---

## Redis keys

| key | type | purpose | ops |
|-----|------|---------|-----|
| `feed:{user_id}` | ZSET | precomputed home feed; score=ts, member=post_id | `ZADD`, `ZREVRANGE`, `ZREMRANGEBYRANK ... 0 -801` |
| `post:{id}` | string (JSON) | stable post blob (text, media keys, author id) | `MGET`, `SET … EX` (jittered TTL) |
| `user:{id}` | string (JSON) | author snapshot (name, avatar) | `MGET`, `SET … EX` |
| `post:{id}:likes` | string (int) | live like counter | `INCR`/`DECR`; flushed to `posts.like_count` |
| `post:{id}:comments` | string (int) | live comment counter | same |
| `lock:post:{id}` | string | single-flight rebuild lock | `SET … NX EX 5` |

Policy: `maxmemory` + `allkeys-lru` (or `volatile-lru`) eviction. Post blobs are
cached **once by post_id**, shared across all feeds — cache size tracks the
working set of hot posts, not `users × 20`.

---

## Write path — create a post

```
1. Client uploads media directly to the bucket (presigned; see Media below),
   receives upload_ids whose post_media rows are state=ready.

2. POST /api/v1/posts        (synchronous, one Postgres transaction)
   BEGIN
     INSERT posts (...)                          -- author, caption, visibility
     UPDATE post_media SET post_id=? WHERE id IN (upload_ids)   -- attach media
     INSERT outbox (event_type='post.created', payload={post_id, author_id})
   COMMIT                                         -- post + outbox atomic
   -> return the created post (author sees it immediately)

3. Relay (separate process)  polls outbox WHERE processed_at IS NULL
   -> publish fan-out job to the queue
   -> UPDATE outbox SET processed_at = now()

4. Workers (async, at-least-once, idempotent)
   - fan-out:      if author is normal -> for each follower: ZADD feed:{follower}
                   if author is celebrity -> skip (pulled at read time)
   - moderation:   ML/NSFW scan of media -> flip post_media.state
   - notifications: @mentions -> notifications app
```

Guarantees: the **outbox** closes the producer gap (crash between commit and
enqueue); the queue's **at-least-once + ack** closes the consumer gap (worker
crash mid-fan-out). Fan-out is idempotent, so retries are safe.

**Fan-out job detail.** Load follower ids in batches; `ZADD` in pipelined
batches; skip inactive followers (compute their feed lazily on return); cap each
feed with `ZREMRANGEBYRANK`. A celebrity author is flagged
`is_fanout_on_read` (followers over threshold) and is never fanned out.

---

## Read path — render the home feed

```
GET /api/v1/feed

1. Precomputed ids:  ZREVRANGE feed:{user_id} 0 19        -> [post_id...]
2. Celebrity merge:  SELECT id FROM posts
                     WHERE user_id IN (celebs_i_follow)
                       AND created_at > <window> ORDER BY created_at LIMIT N
3. Merge + sort by time + de-dupe -> final ordered id list

4. Hydrate (cache-aside, batched):
   MGET post:{id}...                       -- most hit
   misses -> SELECT * FROM posts WHERE id IN (<misses>)  -- one query
          -> SET post:{id} ... EX (ttl ± jitter)         -- backfill
   author -> MGET user:{id}... (or snapshot embedded in blob)
   counts -> MGET post:{id}:likes / :comments            -- live volatile values
5. Merge blob + author + counts -> render

Cache miss on feed:{user_id} (Redis lost / new user / new follow):
   rebuild from source (recent posts of followees + celebrities) -> populate ZSET
```

**Stampede protection** on hot `post:{id}`:

- **Single-flight lock** — first miss `SET lock:post:{id} … NX`; it rebuilds and
  backfills, others wait briefly or serve stale. One DB query, not thousands.
- **Stale-while-revalidate** — serve the stale blob and refresh in the
  background rather than hard-missing.
- **TTL jitter** — randomized TTLs so a batch of posts don't expire in lockstep.

---

## Like / unlike path

```
POST /api/v1/posts/{id}/like
  - upsert likes row (partial-unique on active) -> INCR post:{id}:likes
POST /api/v1/posts/{id}/unlike
  - soft-delete likes row                       -> DECR post:{id}:likes
Reconciler (periodic): flush post:{id}:likes -> posts.like_count (idempotent);
  on Redis loss, reseed counter from COUNT(*) over active likes.
"Who liked": SELECT ... FROM likes WHERE post_id=? AND deleted_at IS NULL (paged)
```

---

## Media upload (presigned)

```
1. POST /api/v1/media/upload-init   {content_type, size}
   -> server: validate type (whitelist); GENERATE storage_key
              create post_media(state=pending, storage_key)
              build presigned POST (content-length-range, content-type, expiry)
   <- {upload_id, presigned_url, fields}

2. Client PUT/POST bytes DIRECTLY to bucket   -- S3 enforces size/type from policy

3. Finalize (authoritative): bucket event -> server HEADs object (real size/type)
                             -> moderation + thumbnails -> state=ready
   (client confirm also flips UI fast, but the event is source of truth)

4. Orphan cleanup: delete pending uploads that never finalized (TTL)
```

Enforcement lives in the **signed policy** (S3-enforced) + a post-upload `HEAD`
— never in the client.

---

## Scaling ladder (apply in order)

| stage | lever | what it solves | key cost |
|-------|-------|----------------|----------|
| 1 | Vertical scaling + indexing | squeeze one box | finite ceiling |
| 2 | **Redis cache** | absorb hot reads before the DB | extra system; staleness |
| 3 | **Read replicas** | spread read load (~100:1) | replication lag → read-your-writes |
| 4 | **Partition by time** | manage/prune huge tables | still one machine |
| 5 | **Shard** (last resort) | data/writes exceed one primary | cross-shard queries, rebalancing |

**Shard keys:** `posts` & `post_media` on **`user_id`** (co-locate a user's
posts + media + feed → no cross-shard joins); `likes` on **`post_id`** (hot
count query). Time is used for **partitioning**, never as a shard key (write
hotspot on the newest shard). Tooling: Citus (sharded Postgres), or app-level.

---

## Consistency & failure summary

- **Feed** is derived/eventually-consistent — rebuildable from source on loss.
- **Counts** are eventually consistent (Redis → periodic flush; reseed from
  `COUNT(*)` if Redis is lost).
- **Post creation** is strongly consistent (single transaction); **fan-out** is
  at-least-once + idempotent via outbox.
- **Media** finalization is authoritative via bucket events, not client trust.
- **Replicas** lag; route read-your-own-writes to the primary briefly.
