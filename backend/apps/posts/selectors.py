"""Post read queries.

The home feed here is the fan-out-on-read baseline (a plain query). Phase 5
replaces it with precomputed Redis feeds and reuses this query only for rebuilds.
"""
from __future__ import annotations

from apps.follows.selectors import followee_ids
from .models import Post

_HYDRATE = ("user", "user__profile")


def get_post(viewer, post_id: int) -> Post | None:
    post = (
        Post.objects.alive()
        .select_related(*_HYDRATE)
        .prefetch_related("media")
        .filter(pk=post_id)
        .first()
    )
    if post and _can_view(viewer, post):
        return post
    return None


def list_user_posts(viewer, author_id):
    qs = (
        Post.objects.alive()
        .select_related(*_HYDRATE)
        .prefetch_related("media")
        .filter(user_id=author_id)
        .order_by("-created_at", "-id")
    )
    return _visibility_filter(qs, viewer, author_id)


def home_feed(viewer):
    """Chronological feed of posts from everyone the viewer follows.

    The viewer follows these authors, so PUBLIC and FOLLOWERS posts are visible;
    PRIVATE posts must never leak into someone else's feed.
    """
    ids = followee_ids(viewer.id)
    return (
        Post.objects.alive()
        .select_related(*_HYDRATE)
        .prefetch_related("media")
        .filter(user_id__in=ids)
        .exclude(visibility=Post.Visibility.PRIVATE)
        .order_by("-created_at", "-id")
    )


# --- visibility -----------------------------------------------------------

def _can_view(viewer, post: Post) -> bool:
    if post.visibility == Post.Visibility.PUBLIC:
        return True
    if getattr(viewer, "is_anonymous", True):
        return False
    if post.user_id == viewer.id:
        return True
    if post.visibility == Post.Visibility.FOLLOWERS:
        return post.user_id in set(followee_ids(viewer.id))
    return False  # PRIVATE and not the owner


def _visibility_filter(qs, viewer, author_id):
    if getattr(viewer, "is_anonymous", True):
        return qs.filter(visibility=Post.Visibility.PUBLIC)
    if viewer.id == author_id:
        return qs  # owner sees everything, including private
    visible = [Post.Visibility.PUBLIC]
    if author_id in set(followee_ids(viewer.id)):
        visible.append(Post.Visibility.FOLLOWERS)
    return qs.filter(visibility__in=visible)
