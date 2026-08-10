"""Custom manager for the User model (email + username, no separate username-only path)."""
from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """Manager where the primary identifier for auth is the email address."""

    use_in_migrations = True

    def _create_user(self, username: str, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Users must have an email address.")
        if not username:
            raise ValueError("Users must have a username.")

        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username: str, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra)

    def create_superuser(self, username: str, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)

        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(username, email, password, **extra)
