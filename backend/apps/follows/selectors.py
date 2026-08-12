"""Read queries for the follow graph.

Other apps depend on these (domain isolation) rather than querying ``Follow``
directly.
"""
from __future__ import annotations

from .models import Follow


def followee_ids(user_id) -> list:
    """IDs of everyone ``user_id`` follows."""
    return list(
        Follow.objects.filter(follower_id=user_id).values_list(
            "following_id", flat=True
        )
    )


def celebrity_followee_ids(user_id) -> list:
    """IDs of the (few) celebrities ``user_id`` follows.

    These are fanned out on read: the feed merges their recent posts in at query
    time instead of reading them from the precomputed feed (Phase 5).
    """
    return list(
        Follow.objects.filter(
            follower_id=user_id, following__is_fanout_on_read=True
        ).values_list("following_id", flat=True)
    )


def follower_ids(user_id):
    """Iterator over the IDs of everyone who follows ``user_id``.

    Returns a chunked iterator, not a list: a popular account has a very large
    follower set and must never be materialised in memory at once.
    """
    return (
        Follow.objects.filter(following_id=user_id)
        .values_list("follower_id", flat=True)
        .iterator(chunk_size=5000)
    )


def is_following(follower_id, following_id) -> bool:
    return Follow.objects.filter(
        follower_id=follower_id, following_id=following_id
    ).exists()
