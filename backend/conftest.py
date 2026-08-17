"""Project-wide test fixtures."""
from __future__ import annotations

import fakeredis
import pytest

from apps.common import redis as common_redis


@pytest.fixture(autouse=True)
def fake_redis():
    """Back the shared Redis client with an in-memory fake for every test."""
    client = fakeredis.FakeRedis(decode_responses=True)
    common_redis.set_client(client)
    yield client
    common_redis.set_client(None)
