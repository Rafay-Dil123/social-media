from __future__ import annotations

import pytest

from apps.common.models import Outbox
from apps.posts.services import create_post

pytestmark = pytest.mark.django_db


def test_create_post_emits_outbox_row(user):
    post = create_post(user, caption="hi", visibility=0, media_ids=[])
    row = Outbox.objects.get(event_type="post.created")
    assert row.payload["post_id"] == post.id
    assert row.payload["author_id"] == str(user.id)
    assert row.processed_at is None  # relay hasn't run yet


def test_outbox_rolls_back_with_post_on_error(user, monkeypatch):
    # If the post transaction fails, the outbox row must not persist either.
    from apps.posts.services import posts as posts_service

    def boom(*a, **k):
        raise RuntimeError("attach failed")

    monkeypatch.setattr(posts_service, "_attach_media", boom)
    with pytest.raises(RuntimeError):
        create_post(user, caption="x", visibility=0, media_ids=[1])

    assert not Outbox.objects.filter(event_type="post.created").exists()
