from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.common import relay
from apps.common.models import Outbox

pytestmark = pytest.mark.django_db


def test_relay_dispatches_and_marks_processed():
    spy = MagicMock()
    relay.register("test.event", spy)
    Outbox.objects.create(event_type="test.event", payload={"x": 1})

    published = relay.drain_once()

    assert published == 1
    spy.delay.assert_called_once_with(x=1)
    assert Outbox.objects.get(event_type="test.event").processed_at is not None


def test_relay_leaves_unregistered_events_pending():
    Outbox.objects.create(event_type="nobody.handles.this", payload={})
    published = relay.drain_once()
    assert published == 0
    row = Outbox.objects.get(event_type="nobody.handles.this")
    assert row.processed_at is None  # picked up later once a handler registers


def test_relay_redelivery_is_safe_for_registered_processed_rows():
    spy = MagicMock()
    relay.register("test.event2", spy)
    Outbox.objects.create(event_type="test.event2", payload={"y": 2})
    relay.drain_once()
    # A second drain does not re-dispatch already-processed rows.
    assert relay.drain_once() == 0
    spy.delay.assert_called_once()
