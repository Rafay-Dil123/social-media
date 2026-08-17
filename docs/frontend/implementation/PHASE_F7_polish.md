# Phase F7 — Polish: errors, empty states, accessibility, performance

**Goal.** Production-grade edges — resilient failures, no layout shift, accessible
interactions, small fast bundles.

---

## F7.1 Error handling layers

- **Query errors** (failed requests) → inline `ErrorState` + `refetch`. Already in
  each screen (F2/F4).
- **Render crashes** → an **error boundary** per route subtree with a fallback and
  **reset on navigation** (`resetKeys={[pathname]}`), so moving to another route
  recovers.
- **Fatal queries** → opt specific queries into `throwOnError` so they bubble to the
  boundary instead of rendering a broken half-screen.
- **Global mutation errors** → a toast system (`onError` default via QueryClient's
  `MutationCache`), so every failed like/follow/post shows feedback once, centrally.

```ts
new QueryClient({
  mutationCache: new MutationCache({ onError: (err) => toast.error(toMessage(err)) }),
});
```

## F7.2 Empty states everywhere

Feed ("follow people…"), profile ("no posts yet"), likers ("be the first to
like"). Designed components, never blank regions.

## F7.3 Performance

- **Images**: `loading="lazy"`, explicit `width`/`height` (or `aspect-ratio`) from
  the media metadata to prevent layout shift and virtualizer measurement thrash.
- **`React.memo(PostCard)`** keyed by `postId`; stable callbacks (`useCallback`).
- **Route-level code splitting** (F1) + prefetch the feed chunk right after login
  (`import('./features/feed/FeedPage')`).
- **Virtualizer overscan** small (5–8); avoid heavy work in row render.
- **Select narrow data** with React Query `select` so components re-render only on
  the fields they use.
- **Debounce** search/typeahead inputs; **cancel** in-flight requests on unmount
  (React Query does this for queries; use `AbortController` for manual calls).

## F7.4 Accessibility

- Buttons: `aria-pressed` (like), `aria-busy`/`disabled` during mutations.
- Focus management on route change (move focus to the main heading).
- Media `alt` text (from `extra.alt_text` if present); captions readable.
- Keyboard: the whole feed navigable; the pill and composer reachable/operable.
- Respect `prefers-reduced-motion` for any animations.

---

## Edge cases (reviewed)

- **Error boundary that never recovers.** Without `resetKeys`, a crashed screen
  stays crashed even after navigating away. Reset on `pathname`.
- **Stale chunk / import failure.** (F1) Fallback offers reload; a boundary catches
  `import()` rejection after a deploy.
- **Offline / reconnect.** Show an offline banner; React Query refetches on
  reconnect. Queue nothing complex — just surface state.
- **Toast storms.** De-dupe identical mutation errors (e.g., rapid like failures)
  so the user isn't buried; one toast per error kind.
- **Layout shift (CLS).** Media without dimensions is the top cause; always reserve
  space. Skeletons must match final sizes.
- **Double submit / navigation during mutation.** Disable submit while pending;
  warn on unload during uploads (F5).
- **Memory** — revoke object URLs (F5); virtualization keeps DOM small (F2);
  `gcTime` evicts unused queries.
- **Timezone/format** — render `created_at` as relative time ("2h") with a title of
  the absolute local time; the API sends ISO UTC.

## Definition of done

Failures are caught at the right layer and shown once; every screen has loading/
empty/error states; images don't shift layout; interactions are keyboard- and
screen-reader-accessible; the initial bundle is small and the feed is smooth.
