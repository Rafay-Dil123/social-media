"""Serializers = the validation layer for the auth endpoints."""
from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.profiles.serializers import ProfileSerializer

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Public representation of the authenticated user."""

    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "email", "date_joined", "profile")
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(min_length=3, max_length=30)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})

    def validate_username(self, value: str) -> str:
        # Format check only (no DB hit). Uniqueness is enforced by the DB
        # constraint and surfaced by the view's IntegrityError backstop.
        value = value.strip()
        if not value.replace("_", "").isalnum():
            raise serializers.ValidationError(
                "Username may only contain letters, numbers, and underscores."
            )
        return value

    def validate_email(self, value: str) -> str:
        # Normalise only; uniqueness is enforced at the database level.
        return value.strip().lower()

    def validate_password(self, value: str) -> str:
        # Runs Django's configured password validators (length, common, numeric...).
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
