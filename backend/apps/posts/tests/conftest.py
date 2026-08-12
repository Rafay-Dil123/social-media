from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.posts.models import PostMedia


@pytest.fixture
def make_user(db):
    def _make(name: str) -> User:
        return User.objects.create_user(
            username=name, email=f"{name}@x.com", password="pw12345678"
        )

    return _make


@pytest.fixture
def user(make_user) -> User:
    return make_user("alice")


@pytest.fixture
def other(make_user) -> User:
    return make_user("bob")


@pytest.fixture
def client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def ready_media(db):
    def _make(owner, n=1):
        return [
            PostMedia.objects.create(
                owner=owner,
                type=PostMedia.Type.IMAGE,
                storage_key=f"uploads/{owner.id}/{owner.username}-{i}.jpg",
                state=PostMedia.State.READY,
            )
            for i in range(n)
        ]

    return _make
