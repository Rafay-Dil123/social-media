"""Shared Redis client.

A single lazily-built, connection-pooled client reused across the process.
``decode_responses=True`` so string/int values come back decoded. Tests swap the
module-level client for an in-memory fake via ``set_client`` / monkeypatching.
"""
from __future__ import annotations

from django.conf import settings

_client = None


def redis_client():
    global _client
    if _client is None:
        import redis

        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def set_client(client) -> None:
    """Override the shared client (used by tests to inject a fake)."""
    global _client
    _client = client
