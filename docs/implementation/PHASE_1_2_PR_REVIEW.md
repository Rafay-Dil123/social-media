# PR Review — Phase 1 (Posts) + Phase 2 (Media)

Self-review of the implementation landed for Phases 1 and 2 (plus the minimal
Phase 0 prerequisites they depend on). Reviewed against the repo conventions in
`ARCHITECTURE.md` and the phase specs in this folder.

**Verdict:** ✅ Approve. All 36 tests pass (16 existing auth + 20 new), `manage.py
check` clean, migrations complete. One real bug was found and fixed during review
(feed visibility leak). Remaining items are intentional deferrals to later phases.

---

## What was implemented

**Phase 0 (now complete)**
- `apps/common/exceptions.py` — shared `ValidationError`/`NotFound`/`PermissionDenied`/`Conflict` wrapped by the existing error envelope.
- `apps/common/pagination.py` — `TimelineCursorPagination` (keyset).
- `apps/common/permissions.py` — `IsOwnerOrReadOnly`.
- `apps/follows/` — `Follow` edge (unique pair + no-self-follow constraints); selectors (`followee_ids`, `celebrity_followee_ids`, `follower_ids` iterator); idempotent `follow`/`unfollow` that maintain denormalized `follower_count`/`following_count` via atomic `F()` updates and flip `is_fanout_on_read` at `CELEBRITY_THRESHOLD`; endpoints.
- `apps/accounts` — `User` gains `follower_count`, `following_count`, `is_fanout_on_read` (migration `0003`).

> Note: follower counts use direct `F()` updates (correct + simple for the follow
> write rate). They graduate to Redis + flush in Phase 5 only if a celebrity row
> becomes contended — same pattern as the like counters.

**Phase 1 — posts**
- `Post` / `PostMedia` models (BigAutoField, soft delete, visibility, denormalized `media_preview`).
- Serializers split by direction; services (`create_post`, `delete_post`); selectors (`get_post`, `list_user_posts`, `home_feed`) with visibility rules; thin views; URLs; admin.

**Phase 2 — media**
- `services/media.py` — presigned `init_upload`, `confirm_upload`, authoritative `finalize_from_event`, `process_media`, `purge_orphan_uploads`. Lazy boto3 client (MinIO/LocalStack-friendly).
- Upload endpoints; settings for bucket/CDN/limits.

---

## Findings

### 🔴 Fixed during review — feed visibility leak (correctness)
`home_feed` filtered by followees but **not** by visibility, so a **PRIVATE**
post by someone you follow would surface in your feed. Fixed with
`.exclude(visibility=PRIVATE)` and covered by
`test_feed_excludes_followees_private_posts`. (PUBLIC and FOLLOWERS remain
visible, which is correct since the viewer follows the author.)

### 🟢 Verified good
- **Authorization / IDOR:** media attach requires `owner=user` **and**
  `state=READY` **and** `post IS NULL`; delete requires ownership; `get_post`
  enforces per-visibility access; media confirm scoped to owner. Covered by
  `test_create_rejects_foreign_media`, `test_delete_requires_owner`,
  `test_followers_only_post_hidden_from_stranger`.
- **Concurrency:** `_attach_media` uses `select_for_update()` inside the atomic
  create, so the same pending media can't be double-attached to two posts (the
  second caller sees `post__isnull=False` and is rejected).
- **Idempotent follow:** duplicate insert is caught via a **savepoint**
  (`with transaction.atomic()`), avoiding the "broken transaction" trap — this
  was a bug caught by `test_follow_is_idempotent` and fixed before merge.
- **No N+1:** feed/list use `select_related(user, user__profile)` +
  `prefetch_related(media)`; `test_feed_query_count_is_bounded` asserts < 15
  queries regardless of result count.
- **Uploads never touch the app:** bytes go client→bucket; size/type enforced by
  the signed `content-length-range`/`content-type` policy + a post-upload `HEAD`
  (`test_confirm_rejects_oversize_real_file`).
- **Secrets/keys:** storage keys are server-generated and user-namespaced; the
  full URL is built from a stored key at read time (CDN swap = no data migration).

### 🟡 Minor / accepted
- **Sync processing in `confirm_upload`:** moderation/thumbnailing runs in the
  request for now. Intentional — Phase 4 moves `process_media` onto Celery
  (`acks_late`, idempotent). Documented in the module docstring.
- **`process_media` helpers are stubs** (`moderate`/`probe_dimensions`/
  `generate_thumbnail`) with safe defaults. Integration points for Pillow/ffmpeg/
  a moderation API — flagged in code and `PHASE_2`.
- **Create/read visibility asymmetry:** create accepts the integer choice; read
  returns the display string. Harmless, but worth noting for API consumers.
- **Duplicate media_ids** in one create are silently de-duplicated rather than
  rejected. Low impact; could 400 instead.
- **`select_for_update` is a no-op on SQLite** (tests), fully effective on
  Postgres (prod). Acceptable — tests still assert the logical outcome.

### ⚪ Deferred to later phases (by design)
- `like_count`/`comment_count` are static `0` until Phase 3 (Redis counters).
- Feed is the plain query baseline; Phase 5 adds fan-out + hydration/caching.
- No `post.created` outbox emission yet — added in Phase 4.
- S3 event webhook for `finalize_from_event` not wired; `confirm_upload` covers
  local/dev. Add the bucket→queue path with the Phase 4 infra.

---

## Test coverage added

```
apps/follows/tests/test_follows.py   4  (edge, idempotent, self-follow, unfollow)
apps/posts/tests/test_posts.py      11  (create text/media/carousel, reject unready/
                                         foreign, soft-delete, owner-only delete,
                                         followers-only visibility, feed order,
                                         private-feed exclusion, query-count bound)
apps/posts/tests/test_media.py       5  (type reject, init+presigned, confirm→ready,
                                         oversize→failed, orphan purge)
```

Run: `pytest apps/posts apps/follows --ds=config.settings.test`

---

## Follow-ups before this hits production
1. Wire the S3 `ObjectCreated` event → `finalize_from_event` (don't rely on the
   client confirm as the only finalizer).
2. Move `process_media` to Celery (Phase 4) so the request doesn't block on
   moderation/transcoding.
3. Add throttling scopes to the post-create and upload-init endpoints (the auth
   app already has `ScopedRateThrottle` configured).
4. Real `moderate`/`probe_dimensions`/`generate_thumbnail` implementations.
