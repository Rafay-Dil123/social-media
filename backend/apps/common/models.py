"""Abstract base models shared across all domain apps.

These add no tables of their own (``abstract = True``); concrete models inherit
them to get consistent UUID primary keys and created/updated timestamps.
"""
from __future__ import annotations

import uuid

from django.db import models


class UUIDModel(models.Model):
    """Non-enumerable UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Automatic created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
