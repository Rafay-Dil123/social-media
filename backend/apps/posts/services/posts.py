"""Post write operations (business logic lives here, not in views)."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import NotFound, PermissionDenied
from apps.common.models import Outbox
from ..models import Post, PostMedia


@transaction.atomic
def create_post(user, *, caption: str, visibility: int, media_ids: list[int]) -> Post:
    post = Post.objects.create(user=user, caption=caption, visibility=visibility)

    if media_ids:
        _attach_media(user, post, media_ids)

    # Transactional outbox: the intent to fan out commits atomically with the
    # post, so it can never be lost (Phase 4). The relay publishes it to the queue.
    Outbox.objects.create(
        event_type="post.created",
        payload={"post_id": post.id, "author_id": str(user.id)},
    )
    return post


def _attach_media(user, post: Post, media_ids: list[int]) -> None:
    # Only this user's READY, not-yet-attached media may be attached.
    media = list(
        PostMedia.objects.select_for_update().filter(
            id__in=media_ids,
            owner=user,
            post__isnull=True,
            state=PostMedia.State.READY,
        )
    )
    if len(media) != len(set(media_ids)):
        raise PermissionDenied("Some media is missing, not yours, or not ready.")

    # Preserve the client's requested carousel order.
    order = {mid: i for i, mid in enumerate(media_ids)}
    media.sort(key=lambda m: order[m.id])
    for i, m in enumerate(media):
        m.post = post
        m.position = i
    PostMedia.objects.bulk_update(media, ["post", "position"])

    post.media_preview = _preview_from(media[0])
    post.save(update_fields=["media_preview"])


def delete_post(user, post_id: int) -> None:
    post = Post.objects.alive().filter(pk=post_id).first()
    if post is None:
        raise NotFound("Post not found.")
    if post.user_id != user.id:
        raise PermissionDenied()
    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at"])
    # Drop the cached blob so the post disappears from reads immediately.
    from .hydrate import evict_post

    evict_post(post_id)


def _preview_from(m: PostMedia) -> dict:
    return {"type": m.type, "key": m.storage_key, "w": m.width, "h": m.height}
