from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.common.cache import single_flight
from apps.interactions import services as like_services
from apps.posts.models import Post
from apps.posts.services import hydrate

pytestmark = pytest.mark.django_db


def _post(user, caption="p"):
    return Post.objects.create(user=user, caption=caption, visibility=Post.Visibility.PUBLIC)


def test_hydrate_returns_blobs_in_order(user):
    p1, p2 = _post(user, "a"), _post(user, "b")
    out = hydrate.hydrate_posts([p2.id, p1.id])
    assert [b["id"] for b in out] == [p2.id, p1.id]
    assert out[0]["author"]["username"] == user.username


def test_hydrate_second_call_served_from_cache(user):
    p = _post(user)
    hydrate.hydrate_posts([p.id])  # warms the cache
    with CaptureQueriesContext(connection) as ctx:
        out = hydrate.hydrate_posts([p.id])
    assert out[0]["id"] == p.id
    # Cache hit: no post/media query (only the like-count reseed may query once).
    assert len(ctx.captured_queries) <= 1


def test_hydrate_drops_deleted_and_negative_caches(user, fake_redis):
    p = _post(user)
    Post.objects.filter(pk=p.id).update(deleted_at="2020-01-01T00:00:00Z")
    assert hydrate.hydrate_posts([p.id]) == []
    assert fake_redis.get(f"post:{p.id}") == "__missing__"


def test_hydrate_attaches_live_like_count(user):
    p = _post(user)
    like_services.like(user, p.id)
    out = hydrate.hydrate_posts([p.id])
    assert out[0]["like_count"] == 1


def test_hydrate_single_returns_none_for_missing(user):
    assert hydrate.hydrate_single(999999) is None


def test_evict_post_removes_blob(user, fake_redis):
    p = _post(user)
    hydrate.hydrate_posts([p.id])
    assert fake_redis.get(f"post:{p.id}") is not None
    hydrate.evict_post(p.id)
    assert fake_redis.get(f"post:{p.id}") is None


def test_single_flight_builds_once(fake_redis):
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return "value", 60

    assert single_flight("k", build) == "value"
    assert single_flight("k", build) == "value"  # served from cache
    assert calls["n"] == 1
