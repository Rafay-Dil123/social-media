# Phase F2 — Feed (infinite + virtualized, all states)

**Goal.** The core screen: cursor-paginated infinite scroll, virtualized DOM,
normalized cache seeding, and every visible state (loading/empty/error/more).

---

## F2.1 `useFeed` hook (with normalization seeding)

```ts
// src/features/feed/useFeed.ts
export function useFeed() {
  const qc = useQueryClient();
  return useInfiniteQuery({
    queryKey: queryKeys.feed(),
    queryFn: async ({ pageParam }) => {
      const page = await fetchFeed(pageParam);
      // Normalize: seed each post so cards/detail read one canonical entry.
      page.results.forEach(p => qc.setQueryData(queryKeys.post(p.id), p));
      return page;
    },
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,     // number | null; null => stop
    refetchOnWindowFocus: false,                      // tuned in F6 (avoid all-page refetch)
  });
}
```

---

## F2.2 FeedPage — flatten, dedupe, virtualize, trigger

```tsx
// src/features/feed/FeedPage.tsx
export default function FeedPage() {
  const q = useFeed();
  const parentRef = useRef<HTMLDivElement>(null);

  // Flatten pages; dedupe by id defensively (a post can shift between pages).
  const posts = useMemo(() => {
    const seen = new Set<number>();
    return (q.data?.pages ?? []).flatMap(pg => pg.results).filter(p =>
      seen.has(p.id) ? false : (seen.add(p.id), true));
  }, [q.data]);

  const virtualizer = useVirtualizer({
    count: posts.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 420,
    overscan: 6,
    measureElement: (el) => el.getBoundingClientRect().height,  // variable heights
  });

  // Load more when the last virtual row nears the end (NOT a DOM sentinel — a
  // sentinel would be virtualized out of the DOM and never intersect).
  const items = virtualizer.getVirtualItems();
  useEffect(() => {
    const last = items.at(-1);
    if (last && last.index >= posts.length - 4 && q.hasNextPage && !q.isFetchingNextPage) {
      q.fetchNextPage();
    }
  }, [items, posts.length, q.hasNextPage, q.isFetchingNextPage]);

  if (q.status === 'pending') return <FeedSkeleton />;
  if (q.status === 'error')   return <ErrorState onRetry={q.refetch} error={q.error} />;
  if (posts.length === 0)     return <EmptyState title="Your feed is empty"
                                        body="Follow people to see their posts." cta="/u/explore" />;

  return (
    <div ref={parentRef} className="feed-scroll">
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
        {items.map(vi => (
          <div key={posts[vi.index].id} ref={virtualizer.measureElement}
               data-index={vi.index}
               style={{ position: 'absolute', top: 0, transform: `translateY(${vi.start}px)`, width: '100%' }}>
            <PostCard postId={posts[vi.index].id} />
          </div>
        ))}
      </div>
      {q.isFetchingNextPage && <Spinner />}
    </div>
  );
}
```

`PostCard` reads the canonical post from the seeded cache:

```tsx
function PostCard({ postId }: { postId: number }) {
  const { data: post } = usePost(postId);   // reads ['post', id] (seeded by useFeed)
  if (!post) return null;
  return (/* avatar, caption, media carousel, LikeButton */);
}
```

---

## Edge cases (reviewed)

- **Load-more trigger with virtualization.** A DOM `IntersectionObserver`
  sentinel does **not** work with a virtualizer — the sentinel is windowed out of
  the DOM. Trigger off the **last virtual item index** instead (as above).
- **Variable row heights.** Text-only vs image vs carousel posts differ a lot.
  Fixed `estimateSize` causes scroll jump; use `measureElement` so the virtualizer
  measures real heights. Images must declare dimensions (F7) or measurement
  thrashes as they load.
- **Duplicate posts across pages.** New posts arriving can shift the window and
  resurface an id on the next page; dedupe by id when flattening.
- **Empty vs loading.** `status==='pending'` (loading skeleton) is different from
  `success` with `results: []` (empty state). A new user with zero follows hits
  the empty state, not an error.
- **Feed rebuild latency.** The backend rebuilds a missing feed on first read; the
  first request may be a touch slower — the skeleton covers it. Don't add a
  spinner-on-white.
- **Scroll restoration.** Navigating to a post and back should restore position.
  React Query keeps the data; persist the virtualizer's `scrollOffset` (e.g. in a
  ref/sessionStorage keyed by route) and restore on mount, else the user jumps to
  the top.
- **`refetchOnWindowFocus` off here.** A default focus refetch of an infinite
  query refetches **every loaded page** and can disturb scroll; freshness is
  handled deliberately in F6 (newest-page check + pill).
- **`next_cursor` semantics.** `null` means no more pages (`getNextPageParam`
  returns it directly). Never send `cursor` when it's `null` (first page).
- **Key stability.** Use `post.id` as the React key (not the array index) so
  virtualized rows don't swap identity and remount.

## Definition of done

A virtualized, infinite, deduped feed with loading/empty/error/loading-more
states; cards read the normalized cache; scroll position survives detail
round-trips; DOM stays small at any scroll depth.
