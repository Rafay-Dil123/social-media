# Phase F4 — Post detail + profile (two pagination shapes)

**Goal.** Secondary read screens that reuse the normalized cache — and correctly
handle the **second pagination shape** (profile posts use DRF cursor pagination,
not the feed's float cursor).

---

## F4.1 Post detail

```ts
// src/features/posts/usePost.ts
export function usePost(id: number) {
  return useQuery({
    queryKey: queryKeys.post(id),
    queryFn: () => fetchPost(id),
    // If the feed already seeded this id, it renders instantly and revalidates.
  });
}
```

```tsx
// PostDetail.tsx
const { id } = useParams();
const q = usePost(Number(id));
if (q.status === 'pending') return <PostSkeleton />;
if (q.status === 'error')   return <NotFoundOrError error={q.error} />;   // 404 -> friendly
return <PostView post={q.data} />;
```

---

## F4.2 Profile + its posts (DRF cursor — different shape!)

The feed returns `{ results, next_cursor: number }`. **Profile posts**
(`GET /users/{id}/posts/`) use DRF `CursorPagination`, which returns
`{ results, next: <url|null>, previous }` where `next` is a **full URL** with an
opaque cursor. Handle it distinctly:

```ts
// src/api/profiles.ts
export const fetchUserPosts = (userId: string, url?: string) =>
  (url ? api.get(url) : api.get(`/users/${userId}/posts/`)).then(r => r.data);
// -> { results, next, previous }

// src/features/profiles/useUserPosts.ts
export function useUserPosts(userId: string) {
  const qc = useQueryClient();
  return useInfiniteQuery({
    queryKey: queryKeys.userPosts(userId),
    queryFn: async ({ pageParam }) => {
      const page = await fetchUserPosts(userId, pageParam);
      page.results.forEach((p: Post) => qc.setQueryData(queryKeys.post(p.id), p));
      return page;
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next ?? undefined,   // opaque URL, not a number
  });
}
```

```ts
// src/features/profiles/useProfile.ts
export function useProfile(username: string) {
  return useQuery({ queryKey: queryKeys.profile(username), queryFn: () => fetchProfile(username) });
}
```

`ProfilePage` composes header (`useProfile`) + a virtualized grid of
`useUserPosts` (same virtualization pattern as F2) + the follow button
(`useFollow`).

---

## Edge cases (reviewed)

- **Two cursor shapes.** Feed = `next_cursor` (number); profile posts = DRF `next`
  (URL). Do **not** share one `fetch` helper; `getNextPageParam` returns different
  types. This is the single most likely integration bug — called out explicitly.
- **`next` is an absolute URL.** If your axios `baseURL` differs from the API's
  absolute host, passing the full `next` URL may bypass `baseURL`. Either strip to
  a relative path or ensure axios handles absolute URLs consistently.
- **Deep-linking a post not in cache.** `usePost` fetches fresh — works without the
  feed having been visited. Seed still helps when arriving from the feed.
- **Deleted post.** Backend returns 404 → show a friendly "post unavailable", not a
  crash. (Soft-deleted posts are filtered server-side.)
- **Private/followers posts on a profile.** The backend `list_user_posts` already
  filters by viewer relationship; the client just renders what it receives — don't
  assume all of a user's posts are visible.
- **Own profile vs others.** Hide the follow button on your own profile; show an
  "edit profile" affordance instead.
- **`is_following` source.** Comes from the profile payload (add it to the profile
  serializer if missing — a small backend follow-up); `useFollow` updates it
  optimistically.
- **Stale profile counts.** `follower_count` is denormalized server-side; after a
  follow, `onSettled` invalidates the profile to reconcile.

## Backend follow-ups
- Ensure the profile endpoint returns `is_following` for the requesting user.
- Consider unifying pagination (make the feed and list endpoints agree) to remove
  the two-shape footgun.

## Definition of done

Detail and profile screens render from the shared cache, both pagination shapes
work, visibility/ownership/deleted cases are handled, and follow reconciles.
