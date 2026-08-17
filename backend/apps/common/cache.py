"""Cache helpers — single-flight to prevent cache stampedes.

When a hot key expires and many readers miss at once, only the first rebuilds
(behind a short lock); the rest briefly wait and then serve the fresh value. This
turns a thundering herd into a single database rebuild.
"""
from __future__ import annotations

import time

from apps.common.redis import redis_client


def single_flight(key: str, build, *, lock_ttl: int = 5, wait_seconds: float = 2.0):
    """Return the cached value at ``key``; on a miss exactly one caller runs
    ``build()`` (which returns ``(value_str, ttl_seconds)``) and populates it.
    """
    r = redis_client()
    val = r.get(key)
    if val is not None:
        return val

    lock = f"lock:{key}"
    if r.set(lock, "1", nx=True, ex=lock_ttl):
        try:
            value, ttl = build()
            r.set(key, value, ex=ttl)
            return value
        finally:
            r.delete(lock)

    # Someone else is rebuilding — wait briefly for them, then serve.
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(0.05)
        val = r.get(key)
        if val is not None:
            return val

    value, ttl = build()  # rare fallback if the builder is slow
    r.set(key, value, ex=ttl)
    return value
