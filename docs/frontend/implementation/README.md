# Frontend — Detailed Implementation (phase by phase)

Code-level build guides, one file per phase, expanding
`../FRONTEND_IMPLEMENTATION_PLAN.md`. Written against the existing
`frontend/src` (axios `api/client.ts`, `AuthContext`, `ProtectedRoute` already
gated on `initializing`, `api/errors.ts`, React Router v6).

Each file follows the same shape: **goal → files/code → edge cases → gotchas →
definition of done**. The **Edge cases** section in each is the point of this
folder — the happy path is easy; these are what break in production.

## Phases

| Phase | File | Outcome |
|-------|------|---------|
| F0 | `PHASE_F0_foundation.md` | React Query, api layer, shell, types |
| F1 | `PHASE_F1_routing.md` | lazy routes, protected/public guards |
| F2 | `PHASE_F2_feed.md` | infinite + virtualized feed with all states |
| F3 | `PHASE_F3_likes_follows.md` | optimistic interactions + the `liked` gap |
| F4 | `PHASE_F4_detail_profile.md` | detail + profile, two pagination shapes |
| F5 | `PHASE_F5_create_upload.md` | 3-call media orchestration |
| F6 | `PHASE_F6_freshness.md` | new-posts pill, refetch tuning |
| F7 | `PHASE_F7_polish.md` | errors, empty, a11y, performance |

## Cross-cutting decisions (apply everywhere)

- **Layering:** components → custom hooks → `api/` functions → axios. Components
  never import axios or query keys.
- **Server state = React Query; UI state = `useState`/`useReducer`.**
- **Normalize:** lists seed `['post', id]`; components read the canonical entry.
- **Two cursor shapes** (reviewed): the **feed** endpoint returns
  `{ results, next_cursor: number|null }` (a float score); **profile posts** use
  DRF cursor pagination `{ results, next: url|null }`. They are handled
  differently — see F2 vs F4.
- **Add deps:** `@tanstack/react-query`, `@tanstack/react-virtual`,
  `@tanstack/react-query-devtools` (dev).

## Known backend gaps surfaced during this review (tracked, handled client-side)

1. **Per-viewer `liked` is not in the shared post cache.** The hydration blob is
   viewer-agnostic (shared `post:{id}`), so it can't say whether *you* liked a
   post. F3 handles this with a separate viewer-scoped like-state source and
   flags the backend follow-up (`liked` on read, or a "my likes" endpoint).
2. **Feed `like_count` is the lagging mirror.** Reconcile likes from the mutation
   response, not a feed refetch (F3).
3. **Direct-to-bucket upload needs bucket CORS** for the frontend origin and a
   specific multipart field order (F5).
