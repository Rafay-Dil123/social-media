"""DRF authentication class that validates the access-token JWT."""
from __future__ import annotations

import jwt
from rest_framework import authentication, exceptions

from .models import Session
from .services import tokens


class JWTAuthentication(authentication.BaseAuthentication):
    """Reads ``Authorization: Bearer <access_token>`` and resolves the user."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None  # No credentials -> let other authenticators/anon handle it.

        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")

        raw_token = header[1].decode("utf-8")
        try:
            payload = tokens.decode_access_token(raw_token)
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Access token has expired.")
        except jwt.PyJWTError:
            raise exceptions.AuthenticationFailed("Invalid access token.")

        if payload.get("type") != "access":
            raise exceptions.AuthenticationFailed("Invalid token type.")

        # Bind the token to a live session: one joined query validates that the
        # session is still active AND loads the user. Revoking the session
        # (logout, logout-all, reuse detection) invalidates the access token on
        # its next request — not just when it expires.
        session = (
            Session.objects.select_related("user")
            .filter(id=payload["sid"])
            .active()
            .first()
        )
        if session is None or not session.user.is_active:
            raise exceptions.AuthenticationFailed("Session is no longer active.")

        return (session.user, raw_token)

    def authenticate_header(self, request):
        return self.keyword
