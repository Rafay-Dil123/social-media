"""Public user profile — one per user."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Profile(UUIDModel, TimeStampedModel):
    # FK by string (settings.AUTH_USER_MODEL) so this app doesn't import the
    # accounts models directly — keeps the dependency one-directional.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )

    display_name = models.CharField(max_length=50, blank=True)
    bio = models.CharField(max_length=160, blank=True)
    avatar_url = models.URLField(blank=True)

    class Meta:
        db_table = "profiles"

    def __str__(self) -> str:
        return f"Profile<{self.user_id}>"
