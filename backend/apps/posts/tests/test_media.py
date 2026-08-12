from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.posts.models import PostMedia
from apps.posts.services import media as media_service

pytestmark = pytest.mark.django_db


class FakeS3:
    """Minimal stand-in for the boto3 S3 client used in the media service."""

    def __init__(self, size=1234, ctype="image/jpeg"):
        self._size, self._ctype = size, ctype
        self.deleted = []

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        return {"url": f"https://bucket/{Bucket}", "fields": {"key": Key, **Fields}}

    def head_object(self, Bucket, Key):
        return {"ContentLength": self._size, "ContentType": self._ctype}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(media_service, "_client", lambda: fake)
    return fake


def test_init_rejects_unsupported_type(client, fake_s3):
    res = client.post(
        "/api/v1/media/upload-init/",
        {"content_type": "text/plain", "size": 100},
        format="json",
    )
    assert res.status_code == 400


def test_init_creates_pending_and_returns_presigned(client, user, fake_s3):
    res = client.post(
        "/api/v1/media/upload-init/",
        {"content_type": "image/jpeg", "size": 5000},
        format="json",
    )
    assert res.status_code == 201
    assert "url" in res.data and "fields" in res.data
    media = PostMedia.objects.get(pk=res.data["upload_id"])
    assert media.state == PostMedia.State.PENDING
    assert media.owner_id == user.id
    assert media.storage_key.startswith(f"uploads/{user.id}/")


def test_confirm_flips_to_ready(client, user, fake_s3):
    upload_id = client.post(
        "/api/v1/media/upload-init/",
        {"content_type": "image/jpeg", "size": 5000},
        format="json",
    ).data["upload_id"]

    res = client.post(f"/api/v1/media/{upload_id}/confirm/")
    assert res.status_code == 200
    assert PostMedia.objects.get(pk=upload_id).state == PostMedia.State.READY


def test_confirm_rejects_oversize_real_file(client, user, monkeypatch):
    # Declared small, but the real object HEADs as huge -> FAILED.
    big = FakeS3(size=999 * 1024 * 1024, ctype="image/jpeg")
    monkeypatch.setattr(media_service, "_client", lambda: big)
    upload_id = client.post(
        "/api/v1/media/upload-init/",
        {"content_type": "image/jpeg", "size": 5000},
        format="json",
    ).data["upload_id"]

    client.post(f"/api/v1/media/{upload_id}/confirm/")
    assert PostMedia.objects.get(pk=upload_id).state == PostMedia.State.FAILED


def test_orphan_purge_deletes_old_pending(user, fake_s3):
    m = PostMedia.objects.create(
        owner=user, type=PostMedia.Type.IMAGE, storage_key="uploads/x.jpg",
        state=PostMedia.State.PENDING,
    )
    PostMedia.objects.filter(pk=m.pk).update(
        created_at=timezone.now() - timedelta(hours=48)
    )
    removed = media_service.purge_orphan_uploads(older_than_hours=24)
    assert removed == 1
    assert not PostMedia.objects.filter(pk=m.pk).exists()
    assert "uploads/x.jpg" in fake_s3.deleted
