# Running & testing Phase 1 + 2 locally

Two ways to verify: **the automated test suite** (no external services needed)
and **a manual API walkthrough** against a running server.

All commands run from `backend/`.

---

## 0. One-time setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # now includes boto3
```

---

## 1. Automated tests (fastest — start here)

The test settings use in-memory SQLite and mock S3, so **nothing external is
required** (no Postgres, no Redis, no AWS):

```bash
pytest --ds=config.settings.test           # whole suite
pytest apps/posts apps/follows --ds=config.settings.test   # just the new work
pytest apps/posts/tests/test_media.py -v --ds=config.settings.test
```

Expected: **36 passed**. The media tests monkeypatch the S3 client, so they
exercise the presign/confirm/orphan logic without a bucket.

---

## 2. Manual walkthrough against a real server

### 2a. Database

Phase 1/2 need Postgres (the dev settings point at it). Create a database and
point the app at it via env vars (defaults are in `config/settings/base.py`):

```bash
createdb social_dev        # or use your existing DB
export POSTGRES_DB=social_dev
export POSTGRES_USER=$(whoami)
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432

python manage.py migrate
python manage.py runserver          # http://localhost:8000
```

> Tip: `python manage.py createsuperuser` then visit `/admin/` to see Posts,
> PostMedia, and Follows in the admin.

### 2b. Get an access token

```bash
# Register (also returns an access_token in the body)
curl -s -X POST http://localhost:8000/api/v1/auth/register/ \
  -H 'Content-Type: application/json' -H 'Origin: http://localhost:5173' \
  -d '{"username":"alice","email":"alice@example.com","password":"pw12345678"}'

# Save the token from the response:
export TOKEN="<access_token from above>"
AUTH=(-H "Authorization: Bearer $TOKEN")
```

### 2c. Create and read posts

```bash
# Text-only post
curl -s -X POST http://localhost:8000/api/v1/posts/ "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"caption":"hello world"}'

# Read it back (use the id from the create response)
curl -s http://localhost:8000/api/v1/posts/1/ "${AUTH[@]}"

# Home feed (cursor-paginated)
curl -s http://localhost:8000/api/v1/feed/ "${AUTH[@]}"

# Follow another user, then their posts appear in your feed
curl -s -X POST http://localhost:8000/api/v1/users/<other-user-uuid>/follow/ "${AUTH[@]}"
```

### 2d. Media upload (presigned)

Without a bucket you can still see the API shape, but the upload PUT and
`confirm` need somewhere for the bytes to land. Easiest local bucket is **MinIO**:

```bash
docker run -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  quay.io/minio/minio server /data --console-address ":9001"

# Point the app at MinIO + create the bucket in the console (http://localhost:9001)
export AWS_S3_ENDPOINT_URL=http://localhost:9000
export AWS_S3_BUCKET=media
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export MEDIA_CDN_BASE=http://localhost:9000/media
# restart runserver so it picks these up
```

Then the flow:

```bash
# 1) init -> returns {upload_id, url, fields, storage_key}
curl -s -X POST http://localhost:8000/api/v1/media/upload-init/ "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"content_type":"image/jpeg","size":20480}'

# 2) upload the bytes DIRECTLY to the bucket using the returned url+fields
#    (multipart form: all "fields" first, then file=@yourimage.jpg)
curl -s -X POST "<url>" \
  -F key="<fields.key>" -F Content-Type=image/jpeg \
  $(: ...include every other returned field as -F name=value...) \
  -F file=@yourimage.jpg

# 3) confirm -> server HEADs the object, processes, flips state to "ready"
curl -s -X POST http://localhost:8000/api/v1/media/<upload_id>/confirm/ "${AUTH[@]}"

# 4) create a post that attaches the ready media
curl -s -X POST http://localhost:8000/api/v1/posts/ "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"caption":"with a photo","media_ids":[<upload_id>]}'
```

> No MinIO? You can still test everything **except** the actual byte upload by
> running the automated media tests (§1), which mock the bucket. The presign and
> confirm logic is fully covered there.

---

## 3. Endpoint reference (Phase 1 + 2)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/posts/` | create a post (`caption`, `visibility`, `media_ids`) |
| GET | `/api/v1/posts/{id}/` | read a post (visibility-checked) |
| DELETE | `/api/v1/posts/{id}/` | soft-delete (owner only) |
| GET | `/api/v1/users/{uuid}/posts/` | a user's posts |
| GET | `/api/v1/feed/` | home feed (cursor: `?cursor=&limit=`) |
| POST | `/api/v1/users/{uuid}/follow/` | follow |
| DELETE | `/api/v1/users/{uuid}/follow/` | unfollow |
| POST | `/api/v1/media/upload-init/` | get a presigned upload |
| POST | `/api/v1/media/{id}/confirm/` | finalize an upload |

---

## 4. Troubleshooting

- **401 on posts** — missing/expired `Authorization: Bearer` token; re-register
  or log in to get a fresh `access_token`.
- **`relation "posts" does not exist`** — run `python manage.py migrate`, and
  confirm you're pointed at the right database (`POSTGRES_DB`).
- **Media `init` 400 "Unsupported content type"** — only jpeg/png/webp/mp4 are
  allowed (see `MEDIA_UPLOAD["ALLOWED_TYPES"]`).
- **`confirm` fails / state stays pending** — the object isn't in the bucket
  (the direct upload step didn't succeed) or `AWS_S3_ENDPOINT_URL`/bucket is
  misconfigured.
