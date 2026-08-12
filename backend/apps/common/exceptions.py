"""Shared API exceptions used across domain apps.

These are plain DRF ``APIException`` subclasses; the project-wide exception
handler (``apps.accounts.exceptions.api_exception_handler``) wraps any of them in
the standard ``{"error": {"code", "detail"}}`` envelope, so raising one from a
service layer produces a consistent response.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException


class ValidationError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid request."
    default_code = "validation_error"


class NotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Not found."
    default_code = "not_found"


class PermissionDenied(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "You do not have permission to perform this action."
    default_code = "permission_denied"


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"
