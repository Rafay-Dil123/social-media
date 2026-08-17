"""Follow-graph signals.

``user_followed`` fires when a *new* follow edge is created (not on a duplicate).
The feed app connects to it to backfill the new followee's recent posts. Kept as
a signal so ``follows`` stays ignorant of ``feed`` (one-directional dependency).
"""
from __future__ import annotations

from django.dispatch import Signal

# providing_args: follower_id, following_id
user_followed = Signal()
