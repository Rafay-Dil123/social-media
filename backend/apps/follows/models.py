"""The directed follow graph: (follower -> following) edges.

Only the edge is modelled here (Phase 0/1). Denormalized follower counts and the
celebrity ``is_fanout_on_read`` flag are added when the feed fan-out needs them
(Phase 5).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class Follow(UUIDModel):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_set",
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "follows"
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"], name="uniq_follow_pair"
            ),
            models.CheckConstraint(
                check=~models.Q(follower=models.F("following")),
                name="no_self_follow",
            ),
        ]
        indexes = [
            models.Index(fields=["follower", "created_at"]),
            models.Index(fields=["following", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.follower_id} -> {self.following_id}"
