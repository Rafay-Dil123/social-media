# Post System — Detailed Implementation (phase by phase)

Code-level build guides, one file per phase. These expand the high-level
`../POST_SYSTEM_IMPLEMENTATION_PLAN.md` into concrete files, models, services,
serializers, views, tasks, and tests — written to match the existing repo
conventions (see `../../ARCHITECTURE.md`):

- One Django app per domain under `apps/`.
- **Thin views** (parse → call service/selector → return); **fat services**.
- Writes in `services/`, reads in `selectors.py`.
- Consistent `{"error": {"code", "detail"}}` envelope via the shared handler.
- `from __future__ import annotations` at the top of every module.
- Tests live in `apps/<app>/tests/`.

The code snippets are **reference implementations** — accurate enough to type in,
but you should read each phase's "gotchas" before pasting.

## Phases

| Phase | File | Outcome |
|-------|------|---------|
| 0 | `PHASE_0_common_follows.md` | shared plumbing + follow graph |
| 1 | `PHASE_1_posts_crud.md` | posts/media CRUD on Postgres (shippable) |
| 2 | `PHASE_2_media_presigned.md` | direct-to-bucket uploads |
| 3 | `PHASE_3_interactions_counters.md` | likes + Redis counters |
| 4 | `PHASE_4_outbox_queue.md` | crash-safe async pipeline |
| 5 | `PHASE_5_feed_fanout.md` | hybrid fan-out feed (Redis ZSET) |
| 6 | `PHASE_6_hydration_cache.md` | cache-aside hydration + stampede guard |
| 7 | `PHASE_7_db_scaling.md` | replicas → partition → shard |

## Conventions used in the snippets

- **Deviation from repo default:** `Post`, `PostMedia`, `Like` use `BigAutoField`
  primary keys (not `UUIDModel`) — high-volume tables want sequential locality.
  They still inherit `TimeStampedModel` where useful. This is called out in each
  file.
- **Redis client:** a single shared helper `apps/common/redis.py` exposing
  `redis_client()` (a `redis.Redis` from a connection pool). Introduced in
  Phase 3.
- **Settings:** new keys (`AWS_*`, `REDIS_URL`, `CELERY_*`, `FEED_*`) go in
  `config/settings/base.py` and are overridden per environment.
