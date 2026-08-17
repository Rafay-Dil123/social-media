# Phase F6 — Feed freshness (new-posts pill + refetch tuning)

**Goal.** Keep the live feed current without rebuilding the list under the user or
hijacking scroll.

---

## F6.1 Detect new posts (lightweight, background)

Don't refetch the whole infinite list. On focus (and optionally a gentle
interval), fetch **only the newest page** and compare its top id against the top
of what's shown.

```ts
// src/features/feed/useNewPosts.ts
export function useNewPosts(topPostId: number | undefined) {
  return useQuery({
    queryKey: ['feed', 'head'],
    queryFn: () => fetchFeed(null),                 // newest page only
    refetchOnWindowFocus: true,
    refetchInterval: 60_000,                        // optional; pause when hidden
    enabled: topPostId != null,
    select: (page) => {
      const idx = page.results.findIndex(p => p.id === topPostId);
      return idx === -1 ? page.results.length : idx; // # of new posts above the fold
    },
  });
}
```

## F6.2 The pill

```tsx
const topId = posts[0]?.id;
const { data: newCount = 0 } = useNewPosts(topId);
// ...
{newCount > 0 && (
  <button className="new-posts-pill" onClick={onShowNew}>
    {newCount} new post{newCount > 1 ? 's' : ''} ↑
  </button>
)}
```

```ts
function onShowNew() {
  queryClient.invalidateQueries({ queryKey: queryKeys.feed() }); // rebuild page 1
  virtualizer.scrollToIndex(0);                                   // user-initiated jump
}
```

---

## Edge cases (reviewed)

- **Never auto-inject at the top.** Prepending posts above the current scroll
  position shifts everything down and mis-targets taps (content shift). The pill
  makes insertion **user-initiated**; only then scroll to top.
- **Pill count accuracy.** If the top visible post has itself scrolled far down,
  compare by id against `posts[0]`, not the current viewport. If the top id isn't
  in the newest page at all (user is way behind), cap the label ("50+ new").
- **Interval battery/data.** `refetchInterval` keeps polling; React Query pauses
  intervals when the tab is hidden by default — good. Consider focus-only (drop the
  interval) on mobile/data-saver.
- **Don't double-count your own new post.** After `useCreatePost` optimistically
  prepends, `posts[0]` is your new post, so the head-check naturally sees 0 new.
- **Deep-scrolled users.** If the user is 10 pages down, tapping the pill rebuilds
  page 1 and scrolls to top — that's expected. Preserve the old pages in cache so
  scrolling back down is still free.
- **Backend mirror lag.** The head-check reads the same feed endpoint; counts there
  are the reconciled mirror. That's fine for "is there new stuff" detection; exact
  like counts still come from `['post', id]`.
- **Real-time (out of scope).** WebSocket/SSE push for instant pills is a future
  upgrade; the poll-on-focus pill is the pragmatic baseline.

## Definition of done

New posts surface via a tap-to-reveal pill; the deep infinite list is never
force-refetched or auto-shifted; polling is focus-aware; your own new post doesn't
inflate the count.
