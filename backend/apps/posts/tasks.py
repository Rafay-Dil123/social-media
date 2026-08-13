"""Celery tasks for the posts domain (Phase 4).

All tasks are idempotent — the outbox relay delivers at-least-once, so a task may
run more than once for the same input.
"""
from __future__ import annotations

from celery import shared_task


@shared_task(acks_late=True, max_retries=5)
def process_media_task(media_id: int) -> None:
    """Probe/moderate/thumbnail an uploaded object, then flip its state.

    Idempotent: re-running recomputes the same result and sets the same state.
    """
    from .services import media as media_service

    media_service.process_media(media_id)


@shared_task(acks_late=True, max_retries=5)
def on_post_created(post_id: int, author_id: str) -> dict:
    """Handle a newly created post.

    Phase 5 fans this out into follower feeds. For now it's a no-op hook so the
    outbox -> relay -> queue pipeline is exercised end to end.
    """
    return {"post_id": post_id, "author_id": author_id}
