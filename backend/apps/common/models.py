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


class Outbox(models.Model):
    """Transactional outbox (Phase 4).

    A row is written *in the same transaction* as the business change it
    describes (e.g. a new post), so the intent to run downstream work can never
    be lost. A relay process publishes unprocessed rows to the queue and marks
    them done. See ``apps.common.relay``.
    """

    id = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)

    class Meta:
        db_table = "outbox"
        indexes = [
            models.Index(fields=["processed_at", "id"], name="idx_outbox_unprocessed"),
        ]

    def __str__(self) -> str:
        return f"Outbox<{self.id} {self.event_type}>"
