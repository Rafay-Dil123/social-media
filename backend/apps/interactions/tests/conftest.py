from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.posts.models import Post

# ``fake_redis`` comes from the project-root conftest (autouse).


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(username="alice", email="a@x.com", password="pw12345678")


@pytest.fixture
def post(user) -> Post:
    return Post.objects.create(user=user, caption="hello", visibility=Post.Visibility.PUBLIC)
