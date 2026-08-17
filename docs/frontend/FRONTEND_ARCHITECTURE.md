# Frontend Architecture

The design for the React frontend that consumes the Phase 0–6 API (auth, posts,
media, likes, follows, feed). It builds on what already exists —
`api/client.ts` (axios with in-memory access token + refresh cookie),
`AuthContext`, `ProtectedRoute` (already gated on `initializing`), and the
`{"error": {...}}` envelope helper in `api/errors.ts`.

Companion docs: `FRONTEND_IMPLEMENTATION_PLAN.md` (phased build). The reasoning
mirrors the backend design docs; where a decision echoes a backend one, it's
noted.

---

## Stack

- **React 18 + TypeScript + Vite** (existing).
- **React Router v6** (existing) — routing, layout routes, lazy loading.
- **TanStack Query (React Query) v5** — *server-state* cache (to add).
- **@tanstack/react-virtual** — feed virtualization (to add).
- **axios** (existing) — HTTP, already wired for auth/refresh.

---

## The core idea: three kinds of state

Most frontend complexity comes from treating all state the same. There are three
kinds, each with its own tool:

| kind | examples | tool |
|------|----------|------|
| **Server state** (a *cache* of data owned by the server) | feed, a post, like counts, profiles | **React Query** |
| **Global UI state** | current user, toasts, theme | `AuthContext` / small context |
| **Local UI state** | caption text, "is menu open", upload draft | `useState` / `useReducer` |

Rule: **anything from the API is server state and belongs in React Query** — never
hand-rolled with `useEffect` + `useState`, never dumped into Redux/Context. React
Query gives caching, request dedup, background refetch, retries, and optimistic
updates for free (the same primitives you built server-side: a keyed cache,
single-flight dedup, stale-while-revalidate).

---

## Layered data access (thin components, fat hooks)

The frontend analogue of the backend's *thin views, fat services*:

```
components (dumb)  ──►  custom hooks (useFeed, useLikePost)  ──►  api/ functions  ──►  axios
   render only            cache keys, optimistic logic          endpoint wrappers
```

- **`api/` functions** — thin, typed, no React. One function per endpoint
  (`fetchFeed`, `likePost`, `createPost`). If an endpoint changes, fix it here.
- **Custom hooks** (per feature) — wrap React Query around the api functions and
  own the query keys, cache updates, and optimistic logic.
- **Components** — call hooks, render state. They never see axios, query keys, or
  endpoints.

**Query-key factory** — keys defined once so they can't drift:

```ts
export const queryKeys = {
  feed: () => ['feed'] as const,
  post: (id: number) => ['post', id] as const,
  user: (username: string) => ['user', username] as const,
  likers: (id: number) => ['post', id, 'likers'] as const,
};
```

---

## Server-cache shape: normalized (IDs + per-entity)

The same post appears in the feed, a profile, search, and the detail page. If each
list cached *full post objects*, the same post would exist as several independent
copies that drift when its like count changes. So the cache is **normalized**,
exactly like the backend feed storing IDs and hydrating `post:{id}` once:

- Feed/list queries are read as pages of posts, but each post is **also seeded**
  into `['post', id]` as it arrives, and components read the canonical
  `['post', id]`.
- A like updates `['post', id]` **once** → every screen showing that post
  re-renders consistently.

(React Query is not auto-normalized like Apollo; we normalize deliberately by
seeding per-post entries and keying reads on `['post', id]`.)

---

## Mutations: optimistic updates with rollback + reconcile

Likes and follows must feel instant, so they're **optimistic**: update the cache
before the server replies, then reconcile. The non-negotiable part is **rollback**
on failure.

```ts
useMutation({
  mutationFn: () => likePost(postId),
  onMutate: async () => {
    await qc.cancelQueries({ queryKey: queryKeys.post(postId) });
    const prev = qc.getQueryData(queryKeys.post(postId));
    qc.setQueryData(queryKeys.post(postId), p => ({ ...p, like_count: p.like_count + 1, liked: true }));
    return { prev };                                   // snapshot for rollback
  },
  onError: (_e, _v, ctx) => qc.setQueryData(queryKeys.post(postId), ctx.prev),
  onSuccess: (res) => qc.setQueryData(queryKeys.post(postId), p => ({ ...p, like_count: res.like_count })),
});
```

**Reconcile from the response, not a blind refetch.** The `like` endpoint returns
the live count; the *feed* exposes the durable `like_count` mirror that the
backend reconciler only flushes periodically. Invalidating the feed after a like
could refetch the lagging mirror and make the count "bounce". So we write the
mutation's returned `like_count` into the cache instead of invalidating.

---

## Feed rendering

Cursor-paginated infinite scroll + virtualization:

- **`useInfiniteQuery`** accumulates pages; `getNextPageParam` threads the
  backend's `next_cursor` (null → stop). Cursor (not offset) keeps pages stable as
  new posts arrive — the reason the backend uses cursors.
- **IntersectionObserver sentinel** — an invisible element after the last row;
  when it enters the viewport, `fetchNextPage()`. No scroll-event math.
- **Virtualization** (`@tanstack/react-virtual`) — render only the ~visible rows
  (+ overscan); constant DOM size regardless of length (the client echo of the
  ~800-cap Redis feed). Cached pages make scrolling back up free (data stays in
  the query cache; only DOM nodes recycle).

**Freshness:**
- Refetch on window focus / reconnect, but **not** an aggressive full re-fetch of
  a deep infinite list (it refetches every page and disturbs scroll). Prefer a
  light check of the newest page.
- New posts arrive at the **top** but the list appends at the **bottom** → a
  **"N new posts" pill**: detect new items in the background, show a pill, and
  only prepend when the user taps it (never auto-inject above the scroll position
  — that causes content shift).
- `staleTime ≈ 30–60s` for the feed. Stale data still renders instantly
  (stale-while-revalidate); you're tuning *how often it checks*, not whether the
  user waits.

---

## Media upload orchestration

The backend split upload into three calls so the heavy transfer overlaps with the
user typing. The client drives that sequence, starting **when a file is picked**,
not on submit:

```
pick file → URL.createObjectURL(file) preview (instant, local)
          → POST /media/upload-init  → { upload_id, url, fields }
          → PUT bytes directly to bucket (progress bar)
          → POST /media/{upload_id}/confirm  → ready
type caption in parallel …
click "Post" (enabled only when all uploads ready)
          → POST /posts { caption, visibility, media_ids: [upload_id, …] }
          → optimistically prepend to feed
```

- Upload **draft** state is **local UI state** (`useReducer`), not React Query —
  it's an ephemeral form, not server data.
- Each attachment: `{ previewUrl, uploadId, progress, status }` where status ∈
  `uploading | processing | ready | failed`.
- `media_ids` = the `upload_id`s of the `ready` attachments, in display order
  (→ backend carousel `position`).
- Removing a file aborts its upload; the orphaned `pending` media is cleaned up by
  the backend purge job. The "Post" button is disabled until all are `ready`
  (matching the backend's ready/owned/unattached validation, so no 403).

---

## Routing

- **Layout routes** group public vs protected:
  `AuthLayout` (login/signup) and `ProtectedRoute → AppLayout` (feed, new, detail,
  profile).
- **`ProtectedRoute`** (exists) gates on `AuthContext`, and critically **waits on
  `initializing`** before deciding — otherwise a refresh redirects a logged-in
  user to `/login` (the original refresh-bounce bug; the backend half was the
  deduped `/auth/refresh`). Redirect preserves `location` so login returns the
  user where they were.
- **Code splitting** — `React.lazy(() => import(...))` per page + a `Suspense`
  fallback, so a logged-out user downloads only the login chunk; feature code
  loads on navigation. Optionally prefetch the feed chunk after login.

---

## Errors, loading, empty states

- **Six visible states** per data screen: initial-loading (skeletons), empty,
  error (+ retry), success, loading-more (bottom spinner), background-refetching.
- **Skeletons over spinners** — placeholder shapes that reserve layout space, so
  content arrival doesn't shift the page; keep the shell mounted (no full-page
  spinner on navigation).
- **Two failure mechanisms:**
  - *Query error* (`isError`) = a failed request → inline error UI + `refetch`.
  - *Error boundary* = a thrown render exception → fallback UI (prevents white
    screen). Complementary; use both.
- **Empty states** are designed UI (e.g. "Follow people to fill your feed"), not
  blank voids.

---

## Folder structure (feature folders)

```
src/
├── api/
│   ├── client.ts          # axios (exists)
│   ├── errors.ts          # envelope -> message (exists)
│   ├── queryKeys.ts       # key factory
│   ├── posts.ts           # fetchFeed, fetchPost, createPost, like/unlike
│   ├── media.ts           # initUpload, putToBucket, confirmUpload
│   └── follows.ts, profiles.ts
├── auth/                  # AuthContext, ProtectedRoute, useAuth (exist)
├── app/
│   ├── QueryProvider.tsx  # QueryClient + provider + devtools
│   ├── AppLayout.tsx      # nav shell
│   └── ErrorBoundary.tsx
├── features/
│   ├── feed/              # useFeed, FeedPage, NewPostsPill
│   ├── posts/             # usePost, useLikePost, useCreatePost, PostCard,
│   │                        PostDetail, PostComposer, LikeButton
│   ├── media/             # useUpload, UploadTile
│   └── profiles/          # useProfile, useFollow, ProfilePage
├── components/            # shared UI: Skeleton, Spinner, EmptyState, Avatar
├── routes.tsx             # route tree + lazy imports
└── main.tsx
```

Each feature folder is self-contained (hooks + components + types), so a feature
can be understood or removed in one place — mirroring the backend's one-app-per-
domain layout.

---

## Clean-code principles applied

- **One responsibility per layer**: api = requests, hooks = cache/logic,
  components = render. No axios or query keys inside components.
- **Types are shared** (`types.ts`) and derived from API responses; no `any`.
- **Named, per-capability hooks** (`useLikePost`) — logic written once, reused.
- **No duplicated cache logic** — normalization + query-key factory prevent drift.
- **Errors handled at the edges** (query error UI + error boundary), not swallowed.
- **Accessibility & layout stability**: skeletons reserve space; buttons have
  disabled/loading states; images lazy-load.
