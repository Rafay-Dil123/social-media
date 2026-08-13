"""Likes.

Append-only-ish: an unlike is a soft delete so history is preserved, and a
**partial unique index** guarantees at most one *active* like per (user, post)
while allowing the row to be revived on re-like. The live count lives in Redis;
this table is the durable source of truth for "who liked" and uniqueness.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class Like(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="likes"
    )
    post = models.ForeignKey(
        "posts.Post", on_delete=models.CASCADE, related_name="likes"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "likes"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "post"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_active_like",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "deleted_at"]),
            models.Index(fields=["user", "deleted_at"]),
        ]

    def __str__(self) -> str:
        state = "active" if self.deleted_at is None else "removed"
        return f"Like<{self.user_id} -> {self.post_id} ({state})>"
