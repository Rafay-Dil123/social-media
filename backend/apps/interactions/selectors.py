"""Like read queries."""
from __future__ import annotations

from .models import Like


def likers(post_id):
    """Active likers of a post, newest first (paginate at the view)."""
    return (
        Like.objects.filter(post_id=post_id, deleted_at__isnull=True)
        .select_related("user", "user__profile")
        .order_by("-id")
    )


def liked_post_ids(user_id):
    return (
        Like.objects.filter(user_id=user_id, deleted_at__isnull=True)
        .values_list("post_id", flat=True)
    )
