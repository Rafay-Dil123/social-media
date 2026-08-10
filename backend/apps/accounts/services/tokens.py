"""Low-level token primitives.

* Access token  -> short-lived signed JWT (stateless, never stored server-side).
* Refresh token -> long random opaque string; only its SHA-256 hash is stored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID

import jwt
from django.conf import settings
from django.utils import timezone

_CFG = settings.AUTH_TOKENS


def generate_access_token(user_id: UUID, session_id: UUID) -> tuple[str, int]:
    """Return ``(jwt, expires_in_seconds)`` for the given user + session.

    The ``sid`` claim binds the access token to a specific session so it can be
    revoked server-side (see ``authentication.JWTAuthentication``).
    """
    now = timezone.now()
    lifetime = _CFG["ACCESS_TOKEN_LIFETIME"]
    expires_at = now + lifetime

    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iss": _CFG["ISSUER"],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=_CFG["ALGORITHM"])
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> dict:
    """Decode & verify an access JWT. Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[_CFG["ALGORITHM"]],
        issuer=_CFG["ISSUER"],
        options={"require": ["exp", "iat", "sub", "sid"]},
    )


def generate_refresh_token() -> str:
    """A high-entropy opaque refresh token (URL-safe, ~256 bits)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hex digest. Deterministic so we can look it up by hash."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def sliding_expiry(from_time: datetime | None = None) -> datetime:
    return (from_time or timezone.now()) + _CFG["REFRESH_SLIDING_LIFETIME"]


def absolute_expiry(from_time: datetime | None = None) -> datetime:
    return (from_time or timezone.now()) + _CFG["REFRESH_ABSOLUTE_LIFETIME"]
