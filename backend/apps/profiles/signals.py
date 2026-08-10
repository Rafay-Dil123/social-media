"""Auto-create a Profile whenever a User is created.

Lives in the profiles app because profiles owns the Profile model. It listens
to the swappable user model via settings.AUTH_USER_MODEL.
"""
from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_for_new_user(sender, instance, created: bool, **kwargs) -> None:
    if created and not Profile.objects.filter(user=instance).exists():
        Profile.objects.create(
            user=instance, display_name=getattr(instance, "username", "")
        )
