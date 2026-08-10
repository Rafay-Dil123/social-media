# Phase 2 — Media upload (presigned, direct-to-bucket)

**Goal.** Large files go **client → bucket**, never through Django. The API
issues a signed permission slip and finalizes authoritatively.

**New module:** `apps/posts/services/media.py` (+ a couple of endpoints). Uses
`boto3` (S3) or the GCS equivalent.

---

## 2.1 Settings — `config/settings/base.py`

```python
AWS_S3_BUCKET = env("AWS_S3_BUCKET")
AWS_S3_REGION = env("AWS_S3_REGION", default="us-east-1")
MEDIA_CDN_BASE = env("MEDIA_CDN_BASE")          # e.g. https://cdn.example.com
MEDIA_UPLOAD = {
    "PRESIGN_EXPIRY_SECONDS": 300,              # 5 min
    "MAX_IMAGE_BYTES": 15 * 1024 * 1024,        # 15 MB
    "MAX_VIDEO_BYTES": 300 * 1024 * 1024,       # 300 MB
    "ALLOWED_TYPES": {
        "image/jpeg": ("image", "jpg"),
        "image/png":  ("image", "png"),
        "image/webp": ("image", "webp"),
        "video/mp4":  ("video", "mp4"),
    },
}
```

---

## 2.2 Init endpoint — generate key + presigned POST

```python
# apps/posts/services/media.py
from __future__ import annotations

import uuid
import boto3
from django.conf import settings

from apps.common.exceptions import ValidationError
from apps.posts.models import PostMedia

_s3 = boto3.client("s3", region_name=settings.AWS_S3_REGION)
_CFG = settings.MEDIA_UPLOAD


def init_upload(user, *, content_type: str, declared_size: int) -> dict:
    if content_type not in _CFG["ALLOWED_TYPES"]:
        raise ValidationError(f"Unsupported content type: {content_type}")
    kind, ext = _CFG["ALLOWED_TYPES"][content_type]
    max_bytes = _CFG["MAX_IMAGE_BYTES"] if kind == "image" else _CFG["MAX_VIDEO_BYTES"]
    if declared_size <= 0 or declared_size > max_bytes:
        raise ValidationError("File too large or invalid size.")

    # Server owns the key. Unguessable, namespaced by user.
    storage_key = f"uploads/{user.id}/{uuid.uuid4().hex}.{ext}"

    media = PostMedia.objects.create(
        owner=user,
        type=(PostMedia.Type.IMAGE if kind == "image" else PostMedia.Type.VIDEO),
        storage_key=storage_key,
        state=PostMedia.State.PENDING,
    )

    # Presigned POST: S3 enforces these conditions server-side on upload.
    presigned = _s3.generate_presigned_post(
        Bucket=settings.AWS_S3_BUCKET,
        Key=storage_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],   # <-- S3 rejects oversize
        ],
        ExpiresIn=_CFG["PRESIGN_EXPIRY_SECONDS"],
    )
    return {"upload_id": media.id, "url": presigned["url"], "fields": presigned["fields"]}
```

```python
# view
class UploadInitView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "media_init"

    def post(self, request):
        ct = request.data.get("content_type")
        size = int(request.data.get("size", 0))
        return Response(media.init_upload(request.user, content_type=ct, declared_size=size))
```

The client then POSTs the file **directly** to `url` with `fields` + the file.
S3 enforces type and size from the signed conditions — the API never sees bytes.

---

## 2.3 Finalize — authoritative, via bucket event

Do **not** trust a client "done" call as truth. Configure the bucket to emit an
`s3:ObjectCreated:*` event → SNS/SQS/Lambda → a small endpoint or a queue
consumer. On receipt:

```python
def finalize_from_event(storage_key: str) -> None:
    media = PostMedia.objects.filter(storage_key=storage_key,
                                     state=PostMedia.State.PENDING).first()
    if media is None:
        return  # unknown key or already handled — idempotent

    head = _s3.head_object(Bucket=settings.AWS_S3_BUCKET, Key=storage_key)
    real_size = head["ContentLength"]
    real_type = head["ContentType"]

    kind, _ = _CFG["ALLOWED_TYPES"].get(real_type, (None, None))
    max_bytes = _CFG["MAX_IMAGE_BYTES"] if kind == "image" else _CFG["MAX_VIDEO_BYTES"]
    if kind is None or real_size > max_bytes:
        media.state = PostMedia.State.FAILED
        media.save(update_fields=["state"])
        _s3.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=storage_key)
        return

    # Enqueue moderation + thumbnail/transcode; that job flips state -> READY.
    from apps.posts.tasks import process_media           # Phase 4 infra
    process_media.delay(media.id)
```

```python
# apps/posts/tasks.py  (Celery task; queue infra lands in Phase 4)
@shared_task(bind=True, max_retries=5, acks_late=True)
def process_media(self, media_id: int):
    media = PostMedia.objects.get(pk=media_id)
    dims = probe_dimensions(media.storage_key)      # ffprobe / Pillow via a temp fetch
    verdict = moderate(media.storage_key)           # NSFW/ML; sync or external API
    if verdict == "reject":
        media.state = PostMedia.State.FAILED
    else:
        media.width, media.height = dims.get("w"), dims.get("h")
        media.duration_ms = dims.get("duration_ms")
        generate_thumbnail(media.storage_key)
        media.state = PostMedia.State.READY
    media.save()
```

> **Client confirm (optional, for UX only).** A lightweight `POST
> /media/{id}/confirm` can flip a UI spinner, but the **event path is the source
> of truth**. If you support very fast posting, allow attaching media in
> `PENDING` and let the post go live once media reaches `READY` (post shows a
> placeholder meanwhile).

---

## 2.4 Orphan cleanup — scheduled

```python
# management command or Celery beat
def purge_orphan_uploads(older_than_hours=24):
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    orphans = PostMedia.objects.filter(post__isnull=True,
                                       state=PostMedia.State.PENDING,
                                       created_at__lt=cutoff)
    for m in orphans.iterator():
        _s3.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=m.storage_key)
    orphans.delete()
```

---

## 2.5 Tests

- `test_init_rejects_disallowed_type`
- `test_init_returns_presigned_fields_and_creates_pending_media` (mock boto3)
- `test_presigned_conditions_include_content_length_range`
- `test_finalize_flips_to_ready_after_head_and_processing` (mock HEAD)
- `test_finalize_rejects_oversize_real_file`
- `test_create_post_only_attaches_ready_media` (ties to Phase 1)
- `test_orphan_purge_deletes_pending_past_ttl`

Use `moto` (mocks AWS) or stub `_s3` in tests.

---

## Definition of done

Clients upload straight to the bucket under a signed policy; the server enforces
type/size without touching bytes; finalize is authoritative via bucket events +
`HEAD`; orphans are cleaned up. Posts attach only `READY` media.
