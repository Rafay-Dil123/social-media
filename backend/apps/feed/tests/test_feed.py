from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.common import relay
from apps.feed import selectors, store
from apps.feed.rebuild import backfill_follow
from apps.feed.tasks import fanout_post
from apps.follows import services as follow_services
from apps.posts.models import Post
from apps.posts.services import create_post

pytestmark = pytest.mark.django_db


def _user(n: str, *, celebrity: bool = False) -> User:
    u = User.objects.create_user(username=n, email=f"{n}@x.com", password="pw12345678")
    if celebrity:
        User.objects.filter(pk=u.id).update(is_fanout_on_read=True)
        u.refresh_from_db()
    return u


def _feed_ids(user_id):
    return [pid for pid, _ in store.read_entries(user_id, 50)]


def test_fanout_delivers_post_to_follower_feed():
    author, follower = _user("author"), _user("follower")
    follow_services.follow(follower, author.id)
    post = create_post(author, caption="hi", visibility=0, media_ids=[])

    fanout_post(post.id, str(author.id))

    assert post.id in _feed_ids(follower.id)


def test_fanout_via_outbox_relay():
    author, follower = _user("author"), _user("follower")
    follow_services.follow(follower, author.id)
    post = create_post(author, caption="hi", visibility=0, media_ids=[])
    # create_post wrote a post.created outbox row; draining dispatches fan-out.
    relay.drain_once()
    assert post.id in _feed_ids(follower.id)


def test_celebrity_author_not_fanned_out():
    star, follower = _user("star", celebrity=True), _user("follower")
    follow_services.follow(follower, star.id)
    post = create_post(star, caption="hi", visibility=0, media_ids=[])

    fanout_post(post.id, str(star.id))

    assert _feed_ids(follower.id) == []  # pulled at read instead


def test_private_post_not_fanned_out():
    author, follower = _user("author"), _user("follower")
    follow_services.follow(follower, author.id)
    post = create_post(
        author, caption="secret", visibility=Post.Visibility.PRIVATE, media_ids=[]
    )
    fanout_post(post.id, str(author.id))
    assert post.id not in _feed_ids(follower.id)


def test_home_feed_merges_precomputed_and_celebrity():
    viewer = _user("viewer")
    normal, star = _user("normal"), _user("star", celebrity=True)
    follow_services.follow(viewer, normal.id)
    follow_services.follow(viewer, star.id)

    p_normal = create_post(normal, caption="n", visibility=0, media_ids=[])
    relay.drain_once()  # fan out the normal author's post
    p_star = create_post(star, caption="s", visibility=0, media_ids=[])
    relay.drain_once()  # celebrity: no-op fan-out

    ids = [pid for pid, _ in selectors.home_feed_entries(viewer, 20)]
    assert p_normal.id in ids   # from the precomputed ZSET
    assert p_star.id in ids     # merged in live at read time


def test_home_feed_rebuilds_on_missing_key():
    viewer, author = _user("viewer"), _user("author")
    follow_services.follow(viewer, author.id)
    # Post exists but was never fanned out (no relay run) and no feed key yet.
    post = create_post(author, caption="old", visibility=0, media_ids=[])

    ids = [pid for pid, _ in selectors.home_feed_entries(viewer, 20)]
    assert post.id in ids  # rebuilt from source


def test_backfill_injects_recent_posts():
    follower, author = _user("follower"), _user("author")
    post = create_post(author, caption="past", visibility=0, media_ids=[])
    backfill_follow(follower.id, author.id)
    assert post.id in _feed_ids(follower.id)


def test_backfill_skips_celebrity():
    follower, star = _user("follower"), _user("star", celebrity=True)
    create_post(star, caption="past", visibility=0, media_ids=[])
    backfill_follow(follower.id, star.id)
    assert _feed_ids(follower.id) == []


def test_follow_triggers_backfill_via_signal(django_capture_on_commit_callbacks):
    follower, author = _user("follower"), _user("author")
    post = create_post(author, caption="past", visibility=0, media_ids=[])
    with django_capture_on_commit_callbacks(execute=True):
        follow_services.follow(follower, author.id)
    assert post.id in _feed_ids(follower.id)


def test_feed_render_query_count_is_bounded():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    viewer = _user("viewer")
    for i in range(5):
        author = _user(f"a{i}")
        follow_services.follow(viewer, author.id)
        create_post(author, caption=f"p{i}", visibility=0, media_ids=[])
        relay.drain_once()

    c = APIClient(); c.force_authenticate(user=viewer)
    with CaptureQueriesContext(connection) as ctx:
        res = c.get("/api/v1/feed/")
    assert res.status_code == 200
    assert len(ctx.captured_queries) < 15  # bounded regardless of feed size


def test_home_feed_endpoint():
    viewer, author = _user("viewer"), _user("author")
    follow_services.follow(viewer, author.id)
    post = create_post(author, caption="hello", visibility=0, media_ids=[])
    relay.drain_once()

    c = APIClient(); c.force_authenticate(user=viewer)
    res = c.get("/api/v1/feed/")
    assert res.status_code == 200
    ids = [row["id"] for row in res.data["results"]]
    assert post.id in ids
