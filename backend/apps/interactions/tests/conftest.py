from __future__ import annotations

import fakeredis
import pytest

from apps.accounts.models import User
from apps.common import redis as common_redis
from apps.posts.models import Post


@pytest.fixture(autouse=True)
def fake_redis():
    """Swap the shared Redis client for an in-memory fake for every test."""
    client = fakeredis.FakeRedis(decode_responses=True)
    common_redis.set_client(client)
    yield client
    common_redis.set_client(None)


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(username="alice", email="a@x.com", password="pw12345678")


@pytest.fixture
def post(user) -> Post:
    return Post.objects.create(user=user, caption="hello", visibility=Post.Visibility.PUBLIC)
