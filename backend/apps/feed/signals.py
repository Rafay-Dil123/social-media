"""Feed reactions to follow-graph changes.

When a follow edge is created, backfill the new followee's recent posts into the
follower's feed — enqueued **after commit** so a rolled-back follow doesn't
trigger it.
"""
from __future__ import annotations

from django.db import transaction


def on_user_followed(sender, follower_id, following_id, **kwargs) -> None:
    from .tasks import backfill_follow_task

    transaction.on_commit(
        lambda: backfill_follow_task.delay(str(follower_id), str(following_id))
    )
