# Frontend — Phased Implementation Plan

How to build the frontend in `FRONTEND_ARCHITECTURE.md`, phase by phase, on top of
the existing setup (`api/client.ts`, `AuthContext`, `ProtectedRoute`,
`api/errors.ts`, React Router). Code shown as **clean sketches** — accurate to
type in, following the layering (api → hooks → components).

Guiding principle: **build the smallest correct vertical slice, then layer**. Each
phase is shippable.

Existing deps: `react`, `react-dom`, `react-router-dom`, `axios`.
To add: `@tanstack/react-query`, `@tanstack/react-query-devtools`,
`@tanstack/react-virtual`.

```bash
npm i @tanstack/react-query @tanstack/react-virtual
npm i -D @tanstack/react-query-devtools
```

---

## Phase F0 — Foundation (React Query, api layer, shell)

**Goal.** The plumbing every feature needs.

- **QueryClient + provider** with sensible defaults:

```tsx
// app/QueryProvider.tsx
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, gcTime: 5 * 60_000, retry: 1, refetchOnWindowFocus: true },
    mutations: { retry: 0 },
  },
});
export function QueryProvider({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}
    <ReactQueryDevtools initialIsOpen={false} />
  </QueryClientProvider>;
}
```

Wrap it **outside** `AuthProvider` in `main.tsx` so hooks can use the client.

- **Query-key factory** (`api/queryKeys.ts`) — see architecture doc.
- **api/ functions** — thin endpoint wrappers reusing the existing `api` axios
  instance:

```ts
// api/posts.ts
import { api } from './client';
export const fetchFeed  = (cursor?: string) => api.get('/feed/', { params: { cursor } }).then(r => r.data);
export const fetchPost  = (id: number) => api.get(`/posts/${id}/`).then(r => r.data);
export const createPost = (b: CreatePostBody) => api.post('/posts/', b).then(r => r.data);
export const likePost   = (id: number) => api.post(`/posts/${id}/like/`).then(r => r.data);
export const unlikePost = (id: number) => api.delete(`/posts/${id}/like/`).then(r => r.data);
```

- **Shared UI primitives** (`components/`): `Skeleton`, `Spinner`, `EmptyState`,
  `Avatar`, `ErrorState`.
- **`app/ErrorBoundary.tsx`** — catch render crashes with a fallback.
- **Types** (`types.ts`): `Post`, `Media`, `Author`, `FeedPage`, `Profile`,
  derived from the API responses (no `any`).

**Deliverable.** App boots with React Query wired, devtools, shared UI, error
boundary. No feature yet.

---

## Phase F1 — Routing + protected shell + code splitting

**Goal.** Real navigation with lazy-loaded pages.

- **Route tree** with layout routes and `React.lazy`:

```tsx
// routes.tsx
const FeedPage    = lazy(() => import('./features/feed/FeedPage'));
const PostDetail  = lazy(() => import('./features/posts/PostDetail'));
const NewPostPage = lazy(() => import('./features/posts/NewPostPage'));
const ProfilePage = lazy(() => import('./features/profiles/ProfilePage'));

export const AppRoutes = () => (
  <Suspense fallback={<FullPageSkeleton />}>
    <Routes>
      <Route element={<AuthLayout />}>
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<SignUp />} />
      </Route>
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<FeedPage />} />
          <Route path="/new" element={<NewPostPage />} />
          <Route path="/post/:id" element={<PostDetail />} />
          <Route path="/u/:username" element={<ProfilePage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  </Suspense>
);
```

- **`ProtectedRoute`** already correct (waits on `initializing`). Enhance the
  redirect to remember `location` and return there after login.
- **`AppLayout`** — persistent nav shell (`<Outlet/>` for the page); keeps the
  shell mounted so navigation never blanks.

**Deliverable.** Lazy-split pages, protected shell, login round-trips back to the
intended route.

---

## Phase F2 — Feed (infinite scroll + virtualization + skeletons)

**Goal.** The core screen.

- **`useFeed` hook** (`features/feed/useFeed.ts`):

```ts
export function useFeed() {
  const qc = useQueryClient();
  return useInfiniteQuery({
    queryKey: queryKeys.feed(),
    queryFn: async ({ pageParam }) => {
      const page = await fetchFeed(pageParam);
      page.results.forEach((p: Post) => qc.setQueryData(queryKeys.post(p.id), p)); // seed normalization
      return page;
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
```

- **`FeedPage`** — flatten pages, virtualize, sentinel:

```tsx
const { data, fetchNextPage, hasNextPage, isFetchingNextPage, status } = useFeed();
const posts = data?.pages.flatMap(p => p.results) ?? [];
// @tanstack/react-virtual over `posts`; IntersectionObserver sentinel -> fetchNextPage()
```

- **States**: `status==='pending'` → `FeedSkeleton`; `error` → `ErrorState` +
  retry; empty → `EmptyState` ("Follow people to fill your feed"); bottom
  `isFetchingNextPage` → small spinner.
- **`PostCard`** reads the canonical post via `usePost(id)` (from the seeded
  cache), so likes stay consistent.

**Deliverable.** A virtualized, infinite, skeleton-backed feed.

---

## Phase F3 — Likes & follows (optimistic hooks)

**Goal.** Instant interactions, done once, reused everywhere.

- **`useLikePost`** — optimistic bump + rollback + reconcile-from-response (see
  architecture doc). `LikeButton` is dumb: `const like = useLikePost(id)`.
- **`useFollow`** — same pattern on the profile; optimistic follower delta.
- **`usePost(id)`** — `useQuery(queryKeys.post(id), () => fetchPost(id))`, used by
  cards and detail so everything reads one cache entry.

**Deliverable.** Likes/follows feel instant, roll back on failure, never bounce
(reconciled from the endpoint's live count, not the lagging feed mirror).

---

## Phase F4 — Post detail + profile

**Goal.** Secondary read screens reusing the cache.

- **`PostDetail`** — `usePost(id)`; served instantly from the feed-seeded cache,
  background-revalidated.
- **`ProfilePage`** — `useProfile(username)` + a `useUserPosts(userId)` infinite
  query (same pattern as the feed), plus the follow button.
- Loading = skeletons; not-found = friendly 404.

**Deliverable.** Detail and profile screens, cache-shared with the feed.

---

## Phase F5 — Create post + media upload

**Goal.** The three-call orchestration with great UX.

- **`useUpload` reducer** — local draft state; per-file
  `{ previewUrl, uploadId, progress, status }`.
- **api/media.ts**: `initUpload`, `putToBucket` (axios `onUploadProgress`),
  `confirmUpload`.
- **On file pick** → preview via `URL.createObjectURL`, then init → put → confirm
  in the background; `AbortController` per file for removal.
- **`useCreatePost`** — `POST /posts` with the ready `upload_id`s; on success,
  optimistically prepend to `['feed']` and seed `['post', id]`.
- **`PostComposer`** — caption + `UploadTile` grid (progress/processing/ready/
  failed); "Post" disabled until all `ready`.
- Cleanup `revokeObjectURL` on unmount/removal.

**Deliverable.** Compose screen where uploads overlap typing; posting is instant.

---

## Phase F6 — Feed freshness (new-posts pill + refetch tuning)

**Goal.** Keep the live feed current without disrupting scroll.

- Turn **off** aggressive full-list refetch for the deep infinite feed; on focus,
  fetch **only the newest page** in the background.
- Compare newest fetched ids against the top of the visible list → **`NewPostsPill`**
  ("N new posts ↑"). Tapping it prepends and scrolls to top; never auto-inject.
- Tune `staleTime` (~30–60s) for the feed; leave detail/profile longer.

**Deliverable.** New posts surface via a pill; no scroll hijack; controlled
refetching.

---

## Phase F7 — Polish: errors, empty, performance

**Goal.** Production-grade edges.

- **Global error boundary** around routes + per-feature boundaries for isolation;
  `throwOnError` for fatal queries.
- **Empty states** everywhere (feed, profile, likers).
- **Performance**: image `loading="lazy"` + width/height to avoid layout shift;
  `React.memo` on `PostCard`; stable `queryKey`s; prefetch the feed chunk after
  login; keep the virtualizer overscan small.
- **Accessibility**: focus management on route change, alt text, button
  `aria-busy`/disabled during mutations.

**Deliverable.** Resilient, fast, accessible app.

---

## Build order at a glance

```
F0 foundation (React Query, api, shell)     ← plumbing
F1 routing + protected + lazy               ← navigation
F2 feed (infinite + virtual + skeletons)    ← core screen   ← ship
F3 likes/follows (optimistic)               ← interactions
F4 detail + profile                         ← read screens
F5 create post + media upload               ← compose
F6 feed freshness (pill)                     ← live updates
F7 errors/empty/perf                         ← polish
```

F0–F2 give a usable read-only feed app; F3–F5 add interaction and posting; F6–F7
make it live and production-grade. Each phase ends with the relevant states
(loading/empty/error) handled, not just the happy path.

---

## Testing (per phase)

- **Component tests** (Vitest + React Testing Library) for hooks and key
  components — mock the api layer, assert loading/empty/error/success render.
- **Optimistic-update tests**: like → immediate UI change → server error → rollback.
- **MSW** (Mock Service Worker) to simulate the API (feed pages, cursor, errors)
  without a backend.
- A smoke test of the upload flow with a mocked bucket PUT.
