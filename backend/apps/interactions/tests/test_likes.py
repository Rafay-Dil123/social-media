from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.common.exceptions import NotFound
from apps.interactions import services
from apps.interactions.models import Like
from apps.interactions.services import LIKES_KEY

pytestmark = pytest.mark.django_db


def test_like_increments_once_and_is_idempotent(user, post, fake_redis):
    services.like(user, post.id)
    services.like(user, post.id)  # idempotent
    assert services.like_count(post.id) == 1
    assert Like.objects.filter(post=post, deleted_at__isnull=True).count() == 1
    assert fake_redis.get(LIKES_KEY.format(post.id)) == "1"


def test_unlike_soft_deletes_and_decrements(user, post):
    services.like(user, post.id)
    services.unlike(user, post.id)
    assert services.like_count(post.id) == 0
    like = Like.objects.get(user=user, post=post)
    assert like.deleted_at is not None


def test_relike_revives_single_row(user, post):
    services.like(user, post.id)
    services.unlike(user, post.id)
    services.like(user, post.id)
    assert services.like_count(post.id) == 1
    assert Like.objects.filter(user=user, post=post).count() == 1  # reused, not new


def test_partial_unique_blocks_two_active_likes(user, post):
    Like.objects.create(user=user, post=post)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Like.objects.create(user=user, post=post)


def test_like_count_reseeds_from_db_on_redis_miss(user, post, fake_redis):
    # Two active likes written directly; Redis has no key yet.
    other = user.__class__.objects.create_user(
        username="bob", email="b@x.com", password="pw12345678"
    )
    Like.objects.create(user=user, post=post)
    Like.objects.create(user=other, post=post)
    fake_redis.delete(LIKES_KEY.format(post.id))

    assert services.like_count(post.id) == 2  # reseeded from COUNT(*)
    assert fake_redis.get(LIKES_KEY.format(post.id)) == "2"


def test_cannot_like_missing_post(user):
    with pytest.raises(NotFound):
        services.like(user, 999999)


def test_reconcile_writes_absolute_value(user, post, fake_redis):
    fake_redis.set(LIKES_KEY.format(post.id), 7)
    updated = services.reconcile_like_counts()
    post.refresh_from_db()
    assert updated == 1
    assert post.like_count == 7


def test_like_endpoint_returns_count(user, post):
    c = APIClient(); c.force_authenticate(user=user)
    res = c.post(f"/api/v1/posts/{post.id}/like/")
    assert res.status_code == 200
    assert res.data["like_count"] == 1
    res = c.delete(f"/api/v1/posts/{post.id}/like/")
    assert res.data["like_count"] == 0
