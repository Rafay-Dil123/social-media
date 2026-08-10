# Phase 7 — Database scaling

**Goal.** Grow past a single Postgres — but only when metrics demand it. Applied
in order: **index/tune → read replicas → partition → shard**. Each step is
reversible and independently valuable.

> Do **not** do this upfront. Phases 1–6 run comfortably on one well-indexed
> Postgres plus Redis for a long time. Add each lever when a specific metric
> (CPU, replication lag, table size, write latency) crosses a threshold.

---

## 7.1 First: measure and tune (no topology change)

- `EXPLAIN (ANALYZE, BUFFERS)` the feed query, the celebrity merge, the
  "who liked" query, and `create_post`.
- Confirm the indexes from Phases 1/3/5 are used (`idx_post_user_created`,
  `idx_post_created`, the partial like indexes).
- Add `pg_stat_statements`; watch for seq scans and long lock waits.
- Tune connection pooling with **PgBouncer** (transaction pooling) before adding
  hardware — app servers × workers can exhaust Postgres connections fast.

## 7.2 Read replicas (spread reads)

Social traffic is ~100:1 read:write. Send reads to replicas, writes to primary.

```python
# config/db_router.py
class ReadReplicaRouter:
    def db_for_read(self, model, **hints):
        return "replica"
    def db_for_write(self, model, **hints):
        return "default"
    def allow_relation(self, *a, **k): return True
```

```python
# settings
DATABASES = {"default": {...primary...}, "replica": {...replica...}}
DATABASE_ROUTERS = ["config.db_router.ReadReplicaRouter"]
```

**Read-your-own-writes.** A replica lags a few ms; a user who just posted may not
see it on a replica. Fixes:

- Pin a user's reads to the **primary** for ~5s after they write (store a
  timestamp in Redis: `just_wrote:{user_id}` with a short TTL; the router/service
  checks it).
- Or serve the author's own new post from the write path's return value / cache,
  not a replica read.

> The feed read path mostly hits **Redis**, not replicas, so lag matters most for
> profile/detail reads. Route deliberately.

## 7.3 Partition big tables by time (still one machine)

`posts` and `likes` grow forever. **Range-partition by month** so old data is
cheap to prune/archive and queries prune to recent partitions.

```sql
-- posts partitioned by created_at (declarative partitioning)
CREATE TABLE posts (...) PARTITION BY RANGE (created_at);
CREATE TABLE posts_2026_07 PARTITION OF posts
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
-- a monthly job pre-creates next month's partition and detaches/archives old ones
```

Django: manage partitions with `django-postgres-extra` or raw migrations. Keep
the PK/indexes per partition. **Time is correct here** — for archiving, not for
distributing write load.

> **Gotcha:** the partition key must be part of the primary key / unique
> constraints. Plan `(id, created_at)` composite keys before converting.

## 7.4 Shard across machines (last resort)

When one primary can't hold the data or absorb the write rate even with the
above. Sharding adds cross-shard queries and rebalancing — reach for it last.

**Shard keys (decided in design):**

| table | shard key | why |
|-------|-----------|-----|
| `posts`, `post_media` | **`user_id`** | co-locate a user's posts + media + feed → user-scoped queries stay single-shard, no cross-shard joins |
| `likes` | **`post_id`** | the hot "who/how many liked this post" query stays single-shard; counts already offloaded to Redis |

- **Hashing:** `hash(shard_key) % N` (or consistent hashing for easier
  rebalancing). Even distribution, no time hotspot.
- **Cross-shard cases to plan explicitly:** global search (fan out + merge, or a
  dedicated search index — see below), "posts I liked" (scatter over like
  shards), analytics (offload to a warehouse).
- **Tooling:** **Citus** (Postgres extension — `SELECT create_distributed_table
  ('posts','user_id')`) so the DB routes/rebalances for you, or app-level routing
  if you want full control. Prefer Citus to avoid hand-rolling a query router.

**Do not** shard on a timestamp — all new writes would hit the newest shard
(write hotspot). Time is for partitioning only.

## 7.5 Adjacent scaling (mention, as needed)

- **Search:** once sharded, cross-shard `caption` search is painful — move search
  to Postgres FTS on a replica first, then a dedicated index (OpenSearch/
  Elasticsearch) fed from the outbox.
- **Counts at extreme scale:** shard the Redis counter or move like events to an
  OLAP store (ClickHouse `SummingMergeTree`) for analytics-grade aggregation.
- **Media:** already scales via the bucket + CDN; nothing DB-bound.

## 7.6 Verification (this phase is measured, not just coded)

- **Load test** (k6/Locust): a synthetic hot post at target likes/sec, and a
  fan-out storm (celebrity crossing the threshold). Confirm p99 read latency and
  DB CPU stay in budget.
- Assert replica routing (writes never hit replica; a post-write read of your own
  content is consistent).
- Verify partition pruning appears in `EXPLAIN` for recent-window feed queries.
- If sharded: test shard-key routing and one cross-shard query path.

---

## Definition of done

A documented, measured ladder: indexed + pooled single node → read replicas with
read-your-writes handling → monthly partitions with an automated partition job →
(only if needed) `user_id`/`post_id` sharding via Citus, with cross-shard cases
planned. Each rung added in response to a metric, not preemptively.
