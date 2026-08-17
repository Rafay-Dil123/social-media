"""Feed regeneration.

The feed is derived data — always rebuildable from the follow graph + recent
posts. Used on a cache miss (Redis lost / new / returning user) and to backfill
a newly-followed account's recent posts.
"""
from __future__ import annotations

from django.conf import settings

from .store import add_to_feeds, write_feed

_CFG = settings.FEED


def rebuild_feed(user) -> None:
    """Repopulate a user's feed ZSET from the source-of-truth query."""
    from apps.posts.selectors import home_feed

    rows = list(
        home_feed(user).values_list("id", "created_at")[: _CFG["MAX_LEN"]]
    )
    entries = [(pid, created.timestamp()) for pid, created in rows]
    write_feed(user.id, entries)


def backfill_follow(follower_id, following_id) -> None:
    """Inject a newly-followed account's recent posts into the follower's feed.

    Skips celebrities (their posts are merged at read time, not stored).
    """
    from apps.accounts.models import User
    from apps.posts.models import Post

    author = User.objects.filter(pk=following_id).first()
    if author is None or author.is_fanout_on_read:
        return

    rows = (
        Post.objects.alive()
        .filter(user_id=following_id)
        .exclude(visibility=Post.Visibility.PRIVATE)
        .order_by("-created_at")
        .values_list("id", "created_at")[: _CFG["BACKFILL_LIMIT"]]
    )
    for pid, created in rows:
        add_to_feeds(pid, created.timestamp(), [follower_id])
