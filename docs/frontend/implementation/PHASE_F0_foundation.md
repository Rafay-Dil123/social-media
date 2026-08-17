# Phase F0 — Foundation (React Query, api layer, shell)

**Goal.** The plumbing every feature needs: a configured React Query client, the
`api/` function layer, the query-key factory, shared UI primitives, an error
boundary, and types.

---

## F0.1 Install + provider

```bash
npm i @tanstack/react-query @tanstack/react-virtual
npm i -D @tanstack/react-query-devtools
```

```tsx
// src/app/QueryProvider.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import type { ReactNode } from 'react';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: (count, err: any) => (err?.response?.status >= 500 ? count < 2 : false),
      refetchOnWindowFocus: true,
    },
    mutations: { retry: 0 },
  },
});

export function QueryProvider({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  );
}
```

```tsx
// src/main.tsx — provider order
<QueryProvider>
  <AuthProvider>
    <App />
  </AuthProvider>
</QueryProvider>
```

> **Gotcha — retry policy.** Don't retry 4xx (a 401/403/404 won't fix itself and
> retrying delays the error UI). Only retry 5xx/network. The predicate above does
> this. Mutations never auto-retry (a retried non-idempotent POST could double-act).

---

## F0.2 api/ layer + query keys

```ts
// src/api/queryKeys.ts
export const queryKeys = {
  feed: () => ['feed'] as const,
  post: (id: number) => ['post', id] as const,
  userPosts: (userId: string) => ['userPosts', userId] as const,
  profile: (username: string) => ['profile', username] as const,
  myLikes: () => ['myLikes'] as const,
};
```

```ts
// src/api/posts.ts
import { api } from './client';
import type { CreatePostBody, FeedPage, Post } from '../types';

export const fetchFeed = (cursor?: number | null): Promise<FeedPage> =>
  api.get('/feed/', { params: cursor != null ? { cursor } : {} }).then(r => r.data);

export const fetchPost = (id: number): Promise<Post> =>
  api.get(`/posts/${id}/`).then(r => r.data);

export const createPost = (body: CreatePostBody): Promise<Post> =>
  api.post('/posts/', body).then(r => r.data);

export const likePost = (id: number): Promise<{ like_count: number }> =>
  api.post(`/posts/${id}/like/`).then(r => r.data);

export const unlikePost = (id: number): Promise<{ like_count: number }> =>
  api.delete(`/posts/${id}/like/`).then(r => r.data);
```

> **Reviewed — cursor type.** The feed's `next_cursor` is a **float score**, not
> an opaque string. `fetchFeed` passes it through as `cursor`. (Profile posts use
> a different, DRF-cursor shape — handled in F4, not here.)

---

## F0.3 Types (no `any`)

```ts
// src/types.ts (add)
export interface Author { id: string; username: string; avatar_url: string; }
export interface Media {
  id: number; type: 'image' | 'video'; url: string;
  position: number; width: number | null; height: number | null; duration_ms: number | null;
}
export interface Post {
  id: number; caption: string; visibility: 'public' | 'followers' | 'private';
  author: Author; media: Media[]; like_count: number; comment_count: number;
  created_at: string;
  liked?: boolean;                 // client-merged (see F3); not from the shared blob
}
export interface FeedPage { results: Post[]; next_cursor: number | null; }
export interface CreatePostBody { caption: string; visibility?: number; media_ids: number[]; }
```

---

## F0.4 Shared UI + error boundary

- `components/Skeleton.tsx`, `Spinner.tsx`, `EmptyState.tsx`, `ErrorState.tsx`
  (message + retry button), `Avatar.tsx`.
- `app/ErrorBoundary.tsx` using `react-error-boundary` (or a small class
  component) with a **reset on route change**:

```tsx
<ErrorBoundary FallbackComponent={Fallback} resetKeys={[location.pathname]}>
```

---

## F0.5 Clear the cache on auth failure / logout

The existing `api/client.ts` calls `onAuthFailure` when refresh ultimately fails,
and `AuthContext` clears the user. **Also clear the query cache**, or another
user's cached posts/feed could linger after logout.

```ts
// where onAuthFailure / logout is handled (AuthContext)
import { queryClient } from '../app/QueryProvider';
// on logout and on auth failure:
queryClient.clear();
```

---

## Edge cases

- **Provider order** — `QueryProvider` outside everything that uses hooks; devtools
  only in `DEV`.
- **No retry on 4xx** — see the retry predicate; otherwise error UIs feel slow and
  a 401 storms the server.
- **Cache leakage between users** — `queryClient.clear()` on logout **and** on the
  refresh-failure path (not just one).
- **`gcTime` vs `staleTime`** — don't conflate: stale governs refetching, gc
  governs eviction of unused data.
- **SSR/hydration** — N/A (Vite SPA); no need for hydration boundaries.
- **Type drift** — derive types from real responses; add a runtime check (zod)
  later if the API changes often.

## Definition of done

App boots with React Query + devtools, the `api/` layer and key factory exist,
shared UI + error boundary are in place, types are defined, and the cache is
cleared on logout/auth-failure. No feature yet.
