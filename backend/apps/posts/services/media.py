"""Phase 2 — direct-to-bucket media uploads.

Flow:
  1. ``init_upload`` validates the declared type/size, generates the storage key
     server-side, creates a PENDING PostMedia row, and returns a presigned POST.
  2. The client uploads bytes straight to the bucket; the bucket enforces the
     signed size/type conditions.
  3. ``confirm_upload`` (client-triggered) or ``finalize_from_event`` (bucket
     event, authoritative) HEADs the object to read the real size/type, runs
     processing, and flips the row to READY.

The boto3 client is created lazily so importing this module never requires AWS
credentials (and so tests can monkeypatch ``_client``). Processing is currently
synchronous; Phase 4 moves it onto a Celery task.
"""
from __future__ import annotations

import uuid

from django.conf import settings

from apps.common.exceptions import NotFound, ValidationError
from apps.posts.models import PostMedia

_CFG = settings.MEDIA_UPLOAD


def _client():
    """Lazily build an S3 client. ``endpoint_url`` supports MinIO/LocalStack."""
    import boto3

    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        endpoint_url=settings.AWS_S3_ENDPOINT_URL or None,
    )


def _limits_for(kind: str) -> int:
    return _CFG["MAX_IMAGE_BYTES"] if kind == "image" else _CFG["MAX_VIDEO_BYTES"]


def init_upload(user, *, content_type: str, declared_size: int) -> dict:
    if content_type not in _CFG["ALLOWED_TYPES"]:
        raise ValidationError(f"Unsupported content type: {content_type}")
    kind, ext = _CFG["ALLOWED_TYPES"][content_type]
    max_bytes = _limits_for(kind)
    if declared_size <= 0 or declared_size > max_bytes:
        raise ValidationError("File size is missing or exceeds the limit.")

    storage_key = f"uploads/{user.id}/{uuid.uuid4().hex}.{ext}"
    media = PostMedia.objects.create(
        owner=user,
        type=(PostMedia.Type.IMAGE if kind == "image" else PostMedia.Type.VIDEO),
        storage_key=storage_key,
        state=PostMedia.State.PENDING,
    )

    presigned = _client().generate_presigned_post(
        Bucket=settings.AWS_S3_BUCKET,
        Key=storage_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],  # bucket rejects oversize
        ],
        ExpiresIn=_CFG["PRESIGN_EXPIRY_SECONDS"],
    )
    return {
        "upload_id": media.id,
        "storage_key": storage_key,
        "url": presigned["url"],
        "fields": presigned["fields"],
    }


def confirm_upload(user, upload_id: int) -> PostMedia:
    """Client-confirm path: verify the object landed, then process it."""
    media = PostMedia.objects.filter(
        id=upload_id, owner=user, state=PostMedia.State.PENDING
    ).first()
    if media is None:
        raise NotFound("Upload not found or already processed.")
    _verify_and_process(media)
    media.refresh_from_db()
    return media


def finalize_from_event(storage_key: str) -> None:
    """Authoritative path, driven by a bucket ObjectCreated event. Idempotent."""
    media = PostMedia.objects.filter(
        storage_key=storage_key, state=PostMedia.State.PENDING
    ).first()
    if media is None:
        return
    _verify_and_process(media)


def _verify_and_process(media: PostMedia) -> None:
    head = _client().head_object(
        Bucket=settings.AWS_S3_BUCKET, Key=media.storage_key
    )
    real_size = head.get("ContentLength", 0)
    real_type = head.get("ContentType", "")
    allowed = _CFG["ALLOWED_TYPES"].get(real_type)
    if allowed is None or real_size <= 0 or real_size > _limits_for(allowed[0]):
        media.state = PostMedia.State.FAILED
        media.save(update_fields=["state"])
        return
    process_media(media.id)


def process_media(media_id: int) -> None:
    """Probe dimensions, moderate, thumbnail, then flip state.

    Phase 4 turns this into an idempotent Celery task. The three helpers below
    are integration points — wire them to Pillow/ffmpeg and a moderation API.
    """
    media = PostMedia.objects.get(pk=media_id)
    if moderate(media.storage_key) == "reject":
        media.state = PostMedia.State.FAILED
        media.save(update_fields=["state"])
        return
    dims = probe_dimensions(media.storage_key)
    media.width = dims.get("w")
    media.height = dims.get("h")
    media.duration_ms = dims.get("duration_ms")
    generate_thumbnail(media.storage_key)
    media.state = PostMedia.State.READY
    media.save(update_fields=["width", "height", "duration_ms", "state"])


def purge_orphan_uploads(older_than_hours: int = 24) -> int:
    """Delete PENDING uploads that were never attached to a post."""
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    orphans = PostMedia.objects.filter(
        post__isnull=True, state=PostMedia.State.PENDING, created_at__lt=cutoff
    )
    client = _client()
    count = 0
    for m in orphans.iterator():
        try:
            client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=m.storage_key)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        count += 1
    orphans.delete()
    return count


# --- integration points (stubs) -------------------------------------------
# Replace with real implementations (see docs/implementation/PHASE_2). Defaults
# are safe: media is accepted and dimensions are left null if not probed.

def moderate(storage_key: str) -> str:  # "ok" | "reject"
    return "ok"


def probe_dimensions(storage_key: str) -> dict:
    return {}


def generate_thumbnail(storage_key: str) -> str | None:
    return None
