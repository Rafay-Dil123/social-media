# Phase F3 — Likes & follows (optimistic) + the `liked` gap

**Goal.** Instant, reusable interactions with rollback — and a real fix for a gap
this review surfaced: the shared post cache can't tell you whether *you* liked a
post.

---

## F3.1 The `liked` gap (important)

The backend hydration blob (`post:{id}`) is **viewer-agnostic** — it's shared
across all users, so it deliberately does **not** contain a per-viewer `liked`
flag. But the UI needs a filled-vs-empty heart per viewer. Three options:

- **A (recommended, no backend change):** maintain viewer like-state on the
  client, seeded lazily. Keep a `Set<number>` of liked post ids in a
  `['myLikes']` query (hydrate from the backend `liked_post_ids` selector exposed
  as `GET /me/likes/` — a small backend add), and merge `liked` into posts at
  render.
- **B:** add `liked` to the post read for authenticated requests (breaks the
  shared cache — each viewer needs a different blob; not worth it).
- **C (interim):** treat `liked` as pure client state toggled optimistically,
  accepting it resets on hard reload until `['myLikes']` loads.

Plan of record: **A** — add a tiny `GET /me/likes/` (returns the viewer's liked
post ids) and merge client-side. Until that endpoint exists, C degrades
gracefully.

```ts
// merge helper used by usePost / PostCard
function withLiked(post: Post, likedIds: Set<number>): Post {
  return { ...post, liked: likedIds.has(post.id) };
}
```

---

## F3.2 `useLikePost` — one toggle, optimistic, rollback, reconcile

```ts
// src/features/posts/useLikePost.ts
export function useLikePost(postId: number) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (liked: boolean) => (liked ? unlikePost(postId) : likePost(postId)),
    onMutate: async (liked) => {
      await qc.cancelQueries({ queryKey: queryKeys.post(postId) });
      const prev = qc.getQueryData<Post>(queryKeys.post(postId));
      qc.setQueryData<Post>(queryKeys.post(postId), p => p && ({
        ...p,
        liked: !liked,
        like_count: p.like_count + (liked ? -1 : 1),
      }));
      return { prev };
    },
    onError: (_e, _liked, ctx) => { if (ctx?.prev) qc.setQueryData(queryKeys.post(postId), ctx.prev); },
    onSuccess: (res) => qc.setQueryData<Post>(queryKeys.post(postId), p =>
      p && ({ ...p, like_count: res.like_count })),   // reconcile from live count, not a refetch
  });
}
```

```tsx
// LikeButton — dumb
function LikeButton({ postId }: { postId: number }) {
  const { data: post } = usePost(postId);
  const like = useLikePost(postId);
  if (!post) return null;
  return (
    <button aria-pressed={post.liked} disabled={like.isPending}
            onClick={() => like.mutate(!!post.liked)}>
      {post.liked ? '♥' : '♡'} {post.like_count}
    </button>
  );
}
```

---

## F3.3 `useFollow` — optimistic follower delta

```ts
export function useFollow(username: string, userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (following: boolean) => (following ? unfollow(userId) : follow(userId)),
    onMutate: async (following) => {
      await qc.cancelQueries({ queryKey: queryKeys.profile(username) });
      const prev = qc.getQueryData<Profile>(queryKeys.profile(username));
      qc.setQueryData<Profile>(queryKeys.profile(username), p => p && ({
        ...p, is_following: !following,
        follower_count: p.follower_count + (following ? -1 : 1),
      }));
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(queryKeys.profile(username), ctx.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.profile(username) }),
  });
}
```

---

## Edge cases (reviewed)

- **Rapid double-tap / spam.** Disable the button while `isPending`, and drive the
  next action off the **current** `liked` state (a toggle), so you never optimistically
  `+2`. `cancelQueries` in `onMutate` prevents an in-flight refetch from clobbering
  the optimistic value.
- **Reconcile from response, not refetch.** The feed's `like_count` is the lagging
  mirror; invalidating it after a like can snap the count back down ("bounce").
  Use the mutation's returned live `like_count` (done in `onSuccess`).
- **`liked` unknown before `['myLikes']` loads.** Default to `false` (empty heart);
  correct once loaded. Never block the feed on it.
- **Follow doesn't instantly change the feed.** Backfill is async (backend
  `on_commit` → Celery), so a newly-followed user's posts appear on the next feed
  refresh, not immediately. Don't optimistically inject their posts.
- **Unauthed / expired session mid-like.** The mutation 401s → axios refresh runs
  → retry or surface an error and roll back. Don't leave the heart filled.
- **Self-actions.** Hide/disable follow on your own profile; liking your own post
  is allowed by the backend (`get_post` passes for the owner).
- **Cross-surface consistency.** Because everything reads `['post', id]`, liking in
  the feed updates the detail page and profile grid automatically — the whole
  reason for normalization (F2).

## Backend follow-up
Add `GET /me/likes/` returning the viewer's active liked post ids (the
`interactions.selectors.liked_post_ids` already exists) so `liked` can be hydrated
rather than guessed.

## Definition of done

Likes/follows are optimistic with rollback, reconciled from the live count, safe
under rapid taps, consistent across every screen, and the per-viewer `liked` state
has a defined source.
