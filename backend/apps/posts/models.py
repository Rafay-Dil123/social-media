"""Post and PostMedia models.

Design note — primary keys
--------------------------
``Post``, ``PostMedia`` use ``BigAutoField`` rather than the project-wide
``UUIDModel``: these are the highest-volume tables and benefit from sequential
insert locality. The bigint id is internal; a non-enumerable public id can be
layered on later. Author/owner FKs still point at the UUID ``User``.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class PostQuerySet(models.QuerySet):
    def alive(self) -> "PostQuerySet":
        return self.filter(deleted_at__isnull=True)


class Post(TimeStampedModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts"
    )

    caption = models.TextField(blank=True)

    class Visibility(models.IntegerChoices):
        PUBLIC = 0, "public"
        FOLLOWERS = 1, "followers"
        PRIVATE = 2, "private"

    visibility = models.SmallIntegerField(
        choices=Visibility.choices, default=Visibility.PUBLIC
    )

    # Denormalized first-media thumbnail so the feed renders without a media join.
    media_preview = models.JSONField(null=True, blank=True)
    # Sparse optional metadata (alt text, location label, etc.).
    extra = models.JSONField(default=dict, blank=True)

    # Durable mirrors of the Redis counters (kept for sorting/analytics; Phase 3).
    like_count = models.BigIntegerField(default=0)
    comment_count = models.BigIntegerField(default=0)

    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        db_table = "posts"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_post_user_created"),
            models.Index(fields=["-created_at", "-id"], name="idx_post_created"),
        ]

    def __str__(self) -> str:
        return f"Post<{self.id} by {self.user_id}>"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class PostMedia(models.Model):
    id = models.BigAutoField(primary_key=True)
    # Null until the media is attached to a post: uploads happen before the post
    # exists (Phase 2 presigned flow).
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="media", null=True, blank=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media"
    )

    class Type(models.IntegerChoices):
        IMAGE = 0, "image"
        VIDEO = 1, "video"

    type = models.SmallIntegerField(choices=Type.choices)
    storage_key = models.TextField()  # bucket path, NOT a full URL
    position = models.SmallIntegerField(default=0)

    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)

    class State(models.IntegerChoices):
        PENDING = 0, "pending"
        READY = 1, "ready"
        FAILED = 2, "failed"

    state = models.SmallIntegerField(choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "post_media"
        indexes = [models.Index(fields=["post", "position"])]

    def __str__(self) -> str:
        return f"PostMedia<{self.id} {self.get_state_display()}>"
