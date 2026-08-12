"""Auth models: User and Session. (Profile now lives in apps.profiles.)

Design notes
------------
* UUID primary keys via ``common.UUIDModel`` (non-enumerable IDs).
* Case-insensitive username uniqueness via a functional constraint; email is
  ``unique=True`` and stored normalised (lower-cased).
* A ``Session`` is one login on one device — it stores only the SHA-256 *hash*
  of the refresh token, plus a sliding expiry and an absolute (hard-cap) expiry.
"""
from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.common.models import UUIDModel

from .managers import UserManager


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):
    """Custom user. Login identifier is the email; username is public-facing."""

    username = models.CharField(max_length=30)
    # Stored normalised (lower-cased) on save, so unique=True enforces
    # case-insensitive uniqueness and satisfies USERNAME_FIELD's uniqueness rule.
    email = models.EmailField(unique=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)

    # Denormalized follow-graph counters, maintained by apps.follows.services.
    # At scale these graduate to Redis + periodic flush (see Phase 5); direct
    # F() updates are correct and simple until a user row is actually contended.
    follower_count = models.BigIntegerField(default=0)
    following_count = models.BigIntegerField(default=0)
    # "Celebrity" switch: once followers cross the threshold, this user's posts
    # are pulled at read time instead of fanned out on write (hybrid feed).
    is_fanout_on_read = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        db_table = "users"
        constraints = [
            models.UniqueConstraint(Lower("username"), name="uniq_user_username_ci"),
        ]

    def __str__(self) -> str:
        return self.username

    def save(self, *args, **kwargs):
        # Normalise email so comparisons and the CI constraint stay consistent.
        self.email = self.__class__.objects.normalize_email(self.email).lower()
        super().save(*args, **kwargs)


class SessionQuerySet(models.QuerySet):
    def active(self) -> "SessionQuerySet":
        now = timezone.now()
        return self.filter(
            revoked_at__isnull=True,
            expires_at__gt=now,
            absolute_expiry__gt=now,
        )


class Session(UUIDModel):
    """One authenticated login on one device.

    Holds the hashed refresh token and the two expiry clocks. The raw refresh
    token is never stored — only its SHA-256 hash.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sessions"
    )

    # SHA-256 hex digest (64 chars). Unique + indexed: this is the lookup key.
    refresh_token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # Previous rotation's hash — enables reuse (theft) detection.
    previous_token_hash = models.CharField(max_length=64, blank=True, db_index=True)

    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now_add=True)

    # Sliding expiry: pushed forward on every rotation.
    expires_at = models.DateTimeField()
    # Absolute hard cap: set once at login, never extended.
    absolute_expiry = models.DateTimeField()

    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = SessionQuerySet.as_manager()

    class Meta:
        db_table = "sessions"
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Session<{self.user.username} @ {self.created_at:%Y-%m-%d}>"

    @property
    def is_active(self) -> bool:
        now = timezone.now()
        return (
            self.revoked_at is None
            and self.expires_at > now
            and self.absolute_expiry > now
        )

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])
