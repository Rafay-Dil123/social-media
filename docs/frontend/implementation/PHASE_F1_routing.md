# Phase F1 — Routing, guards, code splitting

**Goal.** Real navigation: layout routes, lazy-loaded pages, a protected subtree
(reusing the existing `ProtectedRoute`), a public-only guard, and resilient
chunk loading.

---

## F1.1 Lazy route tree

```tsx
// src/routes.tsx
import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { PublicOnlyRoute } from './auth/PublicOnlyRoute';
import { AppLayout } from './app/AppLayout';
import { FullPageSkeleton } from './components/Skeleton';

const FeedPage    = lazy(() => import('./features/feed/FeedPage'));
const PostDetail  = lazy(() => import('./features/posts/PostDetail'));
const NewPostPage = lazy(() => import('./features/posts/NewPostPage'));
const ProfilePage = lazy(() => import('./features/profiles/ProfilePage'));
const Login       = lazy(() => import('./pages/Login'));
const SignUp      = lazy(() => import('./pages/SignUp'));

export function AppRoutes() {
  return (
    <Suspense fallback={<FullPageSkeleton />}>
      <Routes>
        <Route element={<PublicOnlyRoute />}>
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
```

---

## F1.2 Guards

`ProtectedRoute` already exists and **waits on `initializing`** — keep that.
Enhance it to remember where the user was headed:

```tsx
// ProtectedRoute (enhanced)
if (initializing) return <FullPageSkeleton />;
if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
return <Outlet />;
```

```tsx
// src/auth/PublicOnlyRoute.tsx — keep logged-in users out of login/signup
export function PublicOnlyRoute() {
  const { user, initializing } = useAuth();
  if (initializing) return <FullPageSkeleton />;
  return user ? <Navigate to="/" replace /> : <Outlet />;
}
```

Login reads the intended destination and returns there:

```tsx
const from = (location.state as any)?.from?.pathname ?? '/';
// after successful login:
navigate(from, { replace: true });
```

---

## F1.3 App shell

```tsx
// src/app/AppLayout.tsx — persistent nav so navigation never blanks
export function AppLayout() {
  return (
    <div className="app">
      <NavBar />
      <main><Outlet /></main>
    </div>
  );
}
```

---

## Edge cases

- **Refresh-bounce (the original bug)** — guards MUST return early while
  `initializing`. Both `ProtectedRoute` and `PublicOnlyRoute` do. Without it, on a
  refresh the logged-in user momentarily has `user === null` → redirect to login.
- **Return-to after login** — preserve `location` in `state.from`; default to `/`.
  Guard against **open-redirect**: only honor internal paths (starts with `/`, not
  `//` or a URL).
- **Stale chunk after deploy** — a `lazy()` import can 404 if the user has an old
  index.html and the hashed chunk was replaced. Wrap `Suspense` in an error
  boundary whose fallback offers **"reload"** (`window.location.reload()`), and
  catch `import()` rejection.
- **Direct deep-link to a protected route while logged out** → guard redirects to
  login, then returns to the deep link after auth (thanks to `state.from`).
- **Scroll on route change** — reset scroll to top on navigation (a
  `ScrollToTop` effect on `pathname`), except when restoring the feed (F2).
- **404 vs catch-all** — a real "not found" page is friendlier than redirecting
  everything to `/`; keep the catch-all only for truly unknown paths.
- **Suspense fallback** — keep it a skeleton, not a spinner, and keep the nav
  shell where possible so lazy loads don't flash the whole screen.

## Definition of done

Pages are code-split; the protected subtree and public-only routes work; refresh
never bounces a logged-in user; login returns to the intended route; stale-chunk
and deep-link cases are handled.
