# Social Media — Auth Foundation

Production-shaped authentication for a social app: **Django + DRF** backend,
**React + TypeScript (Vite)** frontend. Implements the register and login flows
we designed — JWT access tokens, rotating refresh tokens stored server-side as
hashes, and an httpOnly refresh cookie.

```
social-media/
├── backend/     # Django REST API
└── frontend/    # React + Vite SPA
```

## How auth works

| Token | Lifetime | Where it lives | Purpose |
|-------|----------|----------------|---------|
| **Access** | 15 min | Client memory (React) | Sent as `Authorization: Bearer` on every API call. Stateless JWT — not stored server-side. |
| **Refresh** | 30-day sliding, 90-day hard cap | httpOnly cookie (client) + **SHA-256 hash** in `sessions` table | Mints new access tokens. Rotated on every use. JS can never read it. |

**One login = one session = one refresh token.** Each device gets its own
`sessions` row, so you can revoke one device without touching the others.

**Rotation with reuse detection:** every refresh issues a new refresh token and
remembers the previous hash. If an already-rotated (stolen) token is replayed,
the whole session is revoked.

### Endpoints (`/api/v1/auth/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/register/` | public | Create user + profile + first session |
| POST | `/login/` | public | Verify credentials, open a new session |
| POST | `/refresh/` | refresh cookie | Rotate refresh token, return new access token |
| POST | `/logout/` | refresh cookie | Revoke the current session |
| POST | `/logout-all/` | access token | Revoke every session for the user |
| GET  | `/me/` | access token | Current user + profile |

## Backend setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit SECRET_KEY etc.

docker compose up -d            # Postgres + Redis
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Run the test suite (uses in-memory SQLite, no services needed):

```bash
DJANGO_SETTINGS_MODULE=config.settings.test pytest
```

Prune expired/revoked sessions (schedule this nightly via cron/Celery):

```bash
python manage.py prune_sessions
```

### Settings modules

- `config.settings.dev` — local development (default)
- `config.settings.prod` — HTTPS hardening, HSTS, secure cookies, JSON-only
- `config.settings.test` — SQLite + fast hasher for the test suite

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env            # VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev                     # http://localhost:5173
npm run typecheck               # tsc --noEmit
```

The axios client (`src/api/client.ts`) keeps the access token in memory, attaches
it to requests, and on a `401` silently calls `/auth/refresh/` once (coalescing
concurrent calls) before retrying. `AuthProvider` restores a session on page load
via the refresh cookie, so a returning user stays logged in.

## Security practices baked in

- Argon2 password hashing (OWASP-recommended)
- Refresh tokens stored only as SHA-256 hashes — a DB leak exposes no usable tokens
- Refresh-token rotation + reuse (theft) detection
- httpOnly + SameSite refresh cookie, path-scoped to `/api/v1/auth`, Secure in prod
- Origin check on cookie-based endpoints (CSRF defence)
- Generic, identical errors for bad credentials (no user enumeration)
- DRF throttling on register/login/refresh
- UUID primary keys (non-enumerable)
- Split settings; secrets via environment only

## Production checklist

- [ ] Strong random `SECRET_KEY`; `DEBUG=False`; real `ALLOWED_HOSTS`
- [ ] Serve over HTTPS (`config.settings.prod`); `REFRESH_COOKIE_SECURE=True`
- [ ] Back DRF throttle cache with Redis for multi-process deployments
- [ ] Schedule `prune_sessions`
- [ ] Set `REFRESH_COOKIE_SAMESITE` per your frontend/backend domain topology
```
