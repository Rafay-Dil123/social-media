"""Helpers for the httpOnly refresh-token cookie and CSRF-origin checks."""
from __future__ import annotations

from django.conf import settings
from rest_framework.response import Response

_COOKIE = settings.REFRESH_COOKIE


def set_refresh_cookie(response: Response, raw_token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=_COOKIE["NAME"],
        value=raw_token,
        max_age=max_age_seconds,
        httponly=_COOKIE["HTTPONLY"],
        secure=_COOKIE["SECURE"],
        samesite=_COOKIE["SAMESITE"],
        path=_COOKIE["PATH"],
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_COOKIE["NAME"],
        path=_COOKIE["PATH"],
        samesite=_COOKIE["SAMESITE"],
    )


def get_refresh_cookie(request) -> str | None:
    return request.COOKIES.get(_COOKIE["NAME"])


def origin_allowed(request) -> bool:
    """CSRF defence for cookie-based endpoints: the request Origin (or Referer)
    must match the trusted frontend origin. The httpOnly cookie is auto-sent by
    the browser, so we verify the caller is our own frontend.
    """
    allowed = settings.FRONTEND_ORIGIN
    origin = request.META.get("HTTP_ORIGIN")
    if origin is not None:
        return origin == allowed
    referer = request.META.get("HTTP_REFERER", "")
    return referer.startswith(allowed)
