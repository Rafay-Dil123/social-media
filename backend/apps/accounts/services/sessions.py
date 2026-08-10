"""Session lifecycle: create (login), rotate (refresh), revoke (logout).

The rotation path implements **refresh-token reuse detection**: if a token that
has already been rotated away is presented again, we treat the session as
compromised and revoke it entirely.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..exceptions import InvalidToken, TokenReuseDetected
from ..models import Session, User
from . import tokens


def _client_meta(request) -> tuple[str, str | None]:
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:1024]
    # Respect a single proxy hop; in production configure this to your infra.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return user_agent, ip


@transaction.atomic
def create_session(user: User, request) -> tuple[Session, str]:
    """Create a new session for a login/registration. Returns (session, raw_token)."""
    raw_token = tokens.generate_refresh_token()
    user_agent, ip = _client_meta(request)

    session = Session.objects.create(
        user=user,
        refresh_token_hash=tokens.hash_refresh_token(raw_token),
        user_agent=user_agent,
        ip_address=ip,
        expires_at=tokens.sliding_expiry(),
        absolute_expiry=tokens.absolute_expiry(),
    )
    return session, raw_token


def rotate_session(raw_token: str, request) -> tuple[Session, str]:
    """Validate + rotate a refresh token. Returns (session, new_raw_token).

    Raises ``TokenReuseDetected`` if a stale token is replayed (theft signal),
    or ``InvalidToken`` if the token is unknown/expired/revoked.

    Note: reuse detection commits the revocation in its own transaction *before*
    raising, so the revoke is not rolled back by the raised exception.
    """
    token_hash = tokens.hash_refresh_token(raw_token)

    # 1) Reuse detection: does this token match a *previous* rotation?
    reused = Session.objects.filter(previous_token_hash=token_hash).first()
    if reused is not None:
        # The presented token was already rotated away -> likely stolen.
        # Commit the revocation, then signal the caller.
        reused.revoke()
        raise TokenReuseDetected()

    # 2) Normal path: match + rotate atomically under a row lock.
    with transaction.atomic():
        session = (
            Session.objects.select_for_update()
            .filter(refresh_token_hash=token_hash)
            .first()
        )
        if session is None or not session.is_active:
            raise InvalidToken()

        # Rotate: issue a new token, remember the old hash, bump the sliding clock.
        new_raw_token = tokens.generate_refresh_token()
        session.previous_token_hash = session.refresh_token_hash
        session.refresh_token_hash = tokens.hash_refresh_token(new_raw_token)
        session.last_used_at = timezone.now()
        # Sliding expiry moves forward but never past the absolute cap.
        session.expires_at = min(tokens.sliding_expiry(), session.absolute_expiry)
        session.save(
            update_fields=[
                "previous_token_hash",
                "refresh_token_hash",
                "last_used_at",
                "expires_at",
            ]
        )
    return session, new_raw_token


def revoke_session(raw_token: str) -> None:
    """Revoke the session that owns this refresh token (logout). Idempotent."""
    token_hash = tokens.hash_refresh_token(raw_token)
    session = Session.objects.filter(refresh_token_hash=token_hash).first()
    if session is not None:
        session.revoke()


def revoke_all_sessions(user: User) -> int:
    """Revoke every active session for a user (logout everywhere)."""
    return Session.objects.active().filter(user=user).update(revoked_at=timezone.now())
