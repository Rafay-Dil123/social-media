"""Feed Celery tasks (Phase 5). All idempotent (ZADD of the same member is a
no-op re-set), so at-least-once delivery is safe.
"""
from __future__ import annotations

from celery import shared_task
from django.conf import settings

from apps.follows.selectors import follower_ids
from apps.posts.models import Post
from .store import add_to_feeds

_CFG = settings.FEED


def _is_active(user_id) -> bool:
    # Activity-based skip (don't fan out to long-idle users) is deferred until
    # User has a last_active timestamp; for now everyone is treated as active.
    return True


@shared_task(acks_late=True, max_retries=5)
def fanout_post(post_id: int, author_id: str) -> None:
    post = Post.objects.filter(pk=post_id, deleted_at__isnull=True).first()
    if post is None:
        return
    # PRIVATE posts are never delivered to anyone else's feed.
    if post.visibility == Post.Visibility.PRIVATE:
        return

    from apps.accounts.models import User

    author = User.objects.filter(pk=author_id).first()
    if author is None or author.is_fanout_on_read:
        return  # celebrity: pulled at read time, not fanned out

    score = post.created_at.timestamp()
    batch: list = []
    for uid in follower_ids(author_id):
        if _is_active(uid):
            batch.append(uid)
        if len(batch) >= _CFG["FANOUT_BATCH"]:
            add_to_feeds(post_id, score, batch)
            batch = []
    if batch:
        add_to_feeds(post_id, score, batch)


@shared_task(acks_late=True, max_retries=5)
def backfill_follow_task(follower_id: str, following_id: str) -> None:
    from .rebuild import backfill_follow

    backfill_follow(follower_id, following_id)
