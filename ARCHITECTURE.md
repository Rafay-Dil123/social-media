# Architecture & Scaling Blueprint

The target structure for growing this from an auth foundation into a full social
app. Current code (the `accounts` app) already follows the conventions here; new
features slot in as **one Django app per domain**. Adopt this incrementally —
nothing needs to move all at once.

---

## Backend layout

```
backend/
├── config/                  # project: settings (split), root urls, wsgi/asgi
│   └── urls.py              # mounts /api/v1/ and includes each app's urls
├── apps/                    # all domain apps, namespaced under apps.*
│   ├── common/              # shared building blocks (base models, handler, pagination)
│   ├── accounts/            # auth: User, Session — login/register/refresh/logout
│   ├── profiles/            # profile data + edit
│   ├── follows/            # follow / unfollow graph
│   ├── posts/               # posts + media
│   ├── feed/                # timeline assembly
│   ├── comments/            # comments on posts
│   ├── interactions/        # likes, bookmarks, reactions
│   ├── notifications/       # notification fan-out + read state
│   └── messaging/           # direct messages
└── manage.py
```

> **Migration safety:** when moving an existing app under `apps/`, set
> `label = "accounts"` in its `AppConfig` and keep the explicit `db_table`
> values. The app *label*, migration history, and table names stay identical —
> only Python import paths (`accounts.x` → `apps.accounts.x`) and
> `INSTALLED_APPS` change.

---

## Standard app layout (every domain app looks the same)

```
apps/<domain>/
├── apps.py              # AppConfig (label, ready() for signals)
├── models.py            # inherit base models from common
├── serializers.py       # request/response shapes + field validation
├── views.py             # THIN — HTTP in/out only, no business logic
├── services/            # business logic for writes (create_post, toggle_like…)
├── selectors.py         # read queries (get_feed_for_user…)
├── permissions.py       # object-level permissions
├── urls.py              # routes, included by config/urls.py
├── admin.py
├── migrations/
└── tests/
```

Predictable layout means any feature is easy to find and review, and new
contributors learn one shape.

---

## The `common` app (shared foundation)

Everything reusable lives here so features inherit it instead of re-solving it.

- **Abstract base models**
  - `UUIDModel` — UUID primary key (non-enumerable).
  - `TimeStampedModel` — `created_at` / `updated_at`.
  - Most models inherit both: `class Post(UUIDModel, TimeStampedModel)`.
  - Refactor `User`, `Profile`, `Session` to inherit these (they currently
    redeclare the fields).
- **API error envelope** — move `accounts/exceptions.py`'s
  `api_exception_handler` here so the whole API shares one `{"error": {...}}`
  shape.
- **Pagination** — a shared cursor/limit-offset pagination class.
- **Base permissions** — e.g. `IsOwnerOrReadOnly`.
- **Common mixins / utilities** — `_client_meta` (IP/UA), throttle base classes.

---

## Core conventions

- **Thin views, fat services.** Views parse input, call a service/selector,
  return a response. All business logic sits in `services/` (writes) and
  `selectors.py` (reads). Keeps views tiny as features get complex.
- **Domain isolation.** An app talks to another only through its public
  services/selectors — never by reaching into its models directly. e.g. `feed`
  asks `follows` "who does X follow?" via a selector, not a raw query.
- **API versioning.** `config/urls.py` mounts everything under `/api/v1/`;
  bump to `/api/v2/` only for breaking changes.
- **One serializer per direction when they diverge** (e.g. `PostCreateSerializer`
  vs `PostReadSerializer`) rather than overloading one.
- **Tests live with the app** (`apps/<domain>/tests/`), mirroring the auth suite.

---

## Frontend layout (feature folders, not type folders)

```
frontend/src/
├── api/                 # shared axios client + error helpers  (already have)
├── features/
│   ├── auth/            # AuthContext, useAuth, Login/SignUp, api/auth.ts
│   ├── feed/
│   ├── posts/
│   ├── profiles/
│   └── notifications/
├── components/          # shared/presentational UI
├── routes/              # route config + guards (ProtectedRoute)
├── types/               # shared types (per-feature types can live in the feature)
└── main.tsx
```

Each `features/<x>/` folder is self-contained: its components, hooks, API calls,
and types together — so a feature can be understood (or removed) in one place.

---

## Suggested build order

Each feature is a self-contained app with its own models, endpoints, and tests.

| # | App | Core models | Notes |
|---|-----|-------------|-------|
| 1 | `common` | (abstract bases) | Do this first — everything depends on it. |
| 2 | `profiles` | Profile (move from accounts) | Edit profile, view by username. |
| 3 | `follows` | Follow(follower, following) | Directed graph; unique together. |
| 4 | `posts` | Post, Media | Create/read/delete; author FK to User. |
| 5 | `feed` | (none — reads) | Timeline from follows + posts (fan-out later). |
| 6 | `interactions` | Like, Bookmark | Toggle endpoints; counts on posts. |
| 7 | `comments` | Comment | Threaded optional; FK to Post. |
| 8 | `notifications` | Notification | Fan-out on follow/like/comment. |
| 9 | `messaging` | Conversation, Message | DMs; realtime later via channels. |

---

## What already matches this blueprint

- Split settings (`config/settings/{base,dev,prod,test}.py`).
- Service layer started (`accounts/services/{tokens,sessions}.py`).
- Consistent error envelope + custom exceptions.
- Per-app tests, UUID PKs, thin-ish views.

The main refactors when you start: create `common`, move `accounts` under
`apps/`, and extract `Profile` into its own `profiles` app.
