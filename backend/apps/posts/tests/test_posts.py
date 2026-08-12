from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.follows import services as follow_services
from apps.posts.models import Post, PostMedia

pytestmark = pytest.mark.django_db


def test_create_text_only_post(client, user):
    res = client.post("/api/v1/posts/", {"caption": "hello world"}, format="json")
    assert res.status_code == 201
    assert res.data["caption"] == "hello world"
    assert Post.objects.filter(user=user, caption="hello world").exists()


def test_create_requires_caption_or_media(client):
    res = client.post("/api/v1/posts/", {"caption": "   "}, format="json")
    assert res.status_code == 400


def test_create_with_media_orders_carousel(client, user, ready_media):
    m1, m2 = ready_media(user, 2)
    # Request them in reverse order; positions must follow the request order.
    res = client.post(
        "/api/v1/posts/",
        {"caption": "trip", "media_ids": [m2.id, m1.id]},
        format="json",
    )
    assert res.status_code == 201
    m2.refresh_from_db(); m1.refresh_from_db()
    assert (m2.position, m1.position) == (0, 1)
    post = Post.objects.get(pk=res.data["id"])
    assert post.media_preview["key"] == m2.storage_key


def test_create_rejects_unready_media(client, user):
    pending = PostMedia.objects.create(
        owner=user, type=PostMedia.Type.IMAGE, storage_key="k", state=PostMedia.State.PENDING
    )
    res = client.post(
        "/api/v1/posts/", {"caption": "x", "media_ids": [pending.id]}, format="json"
    )
    assert res.status_code == 403


def test_create_rejects_foreign_media(client, other, ready_media):
    foreign = ready_media(other, 1)[0]
    res = client.post(
        "/api/v1/posts/", {"caption": "x", "media_ids": [foreign.id]}, format="json"
    )
    assert res.status_code == 403


def test_delete_soft_deletes_and_hides(client, user):
    pid = client.post("/api/v1/posts/", {"caption": "bye"}, format="json").data["id"]
    res = client.delete(f"/api/v1/posts/{pid}/")
    assert res.status_code == 204
    assert Post.objects.get(pk=pid).deleted_at is not None
    assert client.get(f"/api/v1/posts/{pid}/").status_code == 404


def test_delete_requires_owner(client, user, other):
    pid = client.post("/api/v1/posts/", {"caption": "mine"}, format="json").data["id"]
    other_client = APIClient(); other_client.force_authenticate(user=other)
    assert other_client.delete(f"/api/v1/posts/{pid}/").status_code == 403
    assert Post.objects.get(pk=pid).deleted_at is None


def test_followers_only_post_hidden_from_stranger(client, user, other):
    pid = client.post(
        "/api/v1/posts/",
        {"caption": "secret", "visibility": Post.Visibility.FOLLOWERS},
        format="json",
    ).data["id"]

    stranger = APIClient(); stranger.force_authenticate(user=other)
    assert stranger.get(f"/api/v1/posts/{pid}/").status_code == 404  # hidden

    follow_services.follow(other, user.id)
    assert stranger.get(f"/api/v1/posts/{pid}/").status_code == 200  # now visible


def test_feed_returns_followees_posts_newest_first(client, user, other):
    follow_services.follow(user, other.id)
    oc = APIClient(); oc.force_authenticate(user=other)
    p1 = oc.post("/api/v1/posts/", {"caption": "first"}, format="json").data["id"]
    p2 = oc.post("/api/v1/posts/", {"caption": "second"}, format="json").data["id"]

    res = client.get("/api/v1/feed/")
    assert res.status_code == 200
    ids = [row["id"] for row in res.data["results"]]
    assert ids[:2] == [p2, p1]  # newest first


def test_feed_excludes_followees_private_posts(client, user, other):
    follow_services.follow(user, other.id)
    oc = APIClient(); oc.force_authenticate(user=other)
    oc.post("/api/v1/posts/", {"caption": "public one"}, format="json")
    oc.post(
        "/api/v1/posts/",
        {"caption": "private one", "visibility": Post.Visibility.PRIVATE},
        format="json",
    )
    res = client.get("/api/v1/feed/")
    captions = [r["caption"] for r in res.data["results"]]
    assert "public one" in captions
    assert "private one" not in captions


def test_feed_query_count_is_bounded(client, user, make_user):
    # 5 followees each with a post; feed rendering must not scale queries with N.
    for i in range(5):
        author = make_user(f"a{i}")
        follow_services.follow(user, author.id)
        ac = APIClient(); ac.force_authenticate(user=author)
        ac.post("/api/v1/posts/", {"caption": f"p{i}"}, format="json")

    with CaptureQueriesContext(connection) as ctx:
        res = client.get("/api/v1/feed/")
    assert res.status_code == 200
    assert len(ctx.captured_queries) < 15  # bounded, no N+1
