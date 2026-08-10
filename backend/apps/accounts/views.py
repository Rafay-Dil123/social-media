"""Auth endpoints: register, login, refresh, logout, logout-all, me.

Token delivery model
--------------------
* Access token  -> returned in the JSON body; the SPA keeps it in memory and
  sends it as ``Authorization: Bearer``.
* Refresh token -> set as an httpOnly, SameSite cookie scoped to /api/v1/auth.
  JavaScript can never read it (XSS-safe); the browser sends it automatically
  to the refresh/logout endpoints only.
"""
from __future__ import annotations

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .cookies import (
    clear_refresh_cookie,
    get_refresh_cookie,
    origin_allowed,
    set_refresh_cookie,
)
from .exceptions import InvalidCredentials, InvalidToken
from .models import User
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer
from .services import sessions, tokens


def _duplicate_field_error(error: IntegrityError) -> ValidationError:
    """Map a unique-constraint violation to a clean per-field 400.

    Works across backends by matching the field name in the constraint text:
    Postgres -> "users_email_key", SQLite -> "users.email"; the username
    functional index is named "uniq_user_username_ci".
    """
    message = str(error).lower()
    if "username" in message:
        return ValidationError({"username": ["This username is already taken."]})
    if "email" in message:
        return ValidationError({"email": ["An account with this email already exists."]})
    # Unknown constraint — surface a generic conflict rather than a 500.
    return ValidationError({"detail": ["Could not create account; please try again."]})


def _issue_tokens(user: User, request, response_body: dict, http_status: int) -> Response:
    """Create a session, attach the access token to the body and the refresh
    token as an httpOnly cookie."""
    session, raw_refresh = sessions.create_session(user, request)
    access_token, expires_in = tokens.generate_access_token(user.id, session.id)

    response_body |= {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }
    response = Response(response_body, status=http_status)

    refresh_max_age = int(tokens._CFG["REFRESH_SLIDING_LIFETIME"].total_seconds())
    set_refresh_cookie(response, raw_refresh, refresh_max_age)
    return response


class RegisterView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "auth_register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            with transaction.atomic():
                # The profiles app auto-creates the Profile via a post_save
                # signal, which fires inside this same transaction.
                user = User.objects.create_user(
                    username=data["username"],
                    email=data["email"],
                    password=data["password"],
                )
        except IntegrityError as exc:
            # DB unique constraint is the source of truth (also wins the race
            # between two simultaneous signups).
            raise _duplicate_field_error(exc) from exc

        body = {"user": UserSerializer(user).data}
        return _issue_tokens(user, request, body, status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "auth_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # ``authenticate`` uses USERNAME_FIELD=email; returns None if inactive/wrong.
        # Email is stored normalised (lower-cased), so match that on lookup.
        user = authenticate(
            request, username=data["email"].strip().lower(), password=data["password"]
        )
        if user is None:
            # Identical generic error for "no such user" and "wrong password".
            raise InvalidCredentials()

        body = {"user": UserSerializer(user).data}
        return _issue_tokens(user, request, body, status.HTTP_200_OK)


class RefreshView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "auth_refresh"

    def post(self, request):
        if not origin_allowed(request):
            raise InvalidToken()

        raw_refresh = get_refresh_cookie(request)
        if not raw_refresh:
            raise InvalidToken()

        # Rotates the token or raises (InvalidToken / TokenReuseDetected).
        session, new_raw_refresh = sessions.rotate_session(raw_refresh, request)
        access_token, expires_in = tokens.generate_access_token(session.user_id, session.id)

        response = Response(
            {"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in},
            status=status.HTTP_200_OK,
        )
        refresh_max_age = int(tokens._CFG["REFRESH_SLIDING_LIFETIME"].total_seconds())
        set_refresh_cookie(response, new_raw_refresh, refresh_max_age)
        return response


class LogoutView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not origin_allowed(request):
            # Still clear the cookie; nothing sensitive is revealed.
            response = Response(status=status.HTTP_204_NO_CONTENT)
            clear_refresh_cookie(response)
            return response

        raw_refresh = get_refresh_cookie(request)
        if raw_refresh:
            sessions.revoke_session(raw_refresh)

        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


class LogoutAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        revoked = sessions.revoke_all_sessions(request.user)
        response = Response({"revoked_sessions": revoked}, status=status.HTTP_200_OK)
        clear_refresh_cookie(response)
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)
