"""Custom API exceptions and a DRF exception handler that returns a consistent
JSON error envelope: ``{"error": {"code": ..., "detail": ...}}``.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class InvalidCredentials(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid credentials."
    default_code = "invalid_credentials"


class InvalidToken(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Refresh token is invalid or expired."
    default_code = "invalid_token"


class TokenReuseDetected(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Session revoked due to suspicious activity. Please sign in again."
    default_code = "token_reuse_detected"


def api_exception_handler(exc, context):
    """Wrap DRF's default handler in a stable ``{"error": {...}}`` envelope."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", "error")
    detail = response.data
    # Flatten DRF's {"detail": "..."} into our envelope; keep field errors as-is.
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        detail = detail["detail"]

    response.data = {"error": {"code": code, "detail": detail}}
    return response
