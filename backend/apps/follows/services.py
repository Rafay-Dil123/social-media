"""Write operations for the follow graph.

The follow *edge* is the source of truth the feed depends on, so it is written
synchronously and transactionally. Denormalized follower/following counts are
maintained here with atomic ``F()`` updates:

* Follows arrive far slower than likes (no single-row hotspot for normal users),
  so a direct DB increment is correct and simple.
* When a *celebrity* is mass-followed the counter row can get hot — at that point
  these counts graduate to Redis + periodic flush (Phase 5), reusing the same
  pattern as the like counters. Until a row is actually contended, this is the
  right default (measure first).

Counts are only touched when the edge actually changes, so following twice or
unfollowing a non-followed user never drifts the numbers.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest

from apps.accounts.models import User
from apps.common.exceptions import ValidationError
from .models import Follow

# Follower count at which a user's posts switch to fan-out-on-read (Phase 5).
CELEBRITY_THRESHOLD = 100_000


@transaction.atomic
def follow(follower, following_id) -> Follow:
    """Create a follow edge. Idempotent: following twice returns the existing
    edge and does not double-count."""
    if follower.id == following_id:
        raise ValidationError("You cannot follow yourself.")

    # get_or_create isolates the insert in its own savepoint, so a duplicate
    # (unique constraint) is handled without breaking the outer transaction.
    edge, created = Follow.objects.get_or_create(
        follower=follower, following_id=following_id
    )
    if created:
        User.objects.filter(pk=following_id).update(
            follower_count=F("follower_count") + 1
        )
        User.objects.filter(pk=follower.id).update(
            following_count=F("following_count") + 1
        )
        _maybe_flip_celebrity(following_id)
    return edge


@transaction.atomic
def unfollow(follower, following_id) -> None:
    """Remove a follow edge. Idempotent: a no-op (no count change) if the edge
    did not exist."""
    deleted, _ = Follow.objects.filter(
        follower=follower, following_id=following_id
    ).delete()
    if deleted:
        # Greatest(..., 0) guards against a count going negative if it ever drifts.
        User.objects.filter(pk=following_id).update(
            follower_count=Greatest(F("follower_count") - 1, Value(0))
        )
        User.objects.filter(pk=follower.id).update(
            following_count=Greatest(F("following_count") - 1, Value(0))
        )


def _maybe_flip_celebrity(user_id) -> None:
    """Promote a user to fan-out-on-read once they cross the threshold.

    Runs a conditional UPDATE so it's a no-op after the first flip (and reads the
    freshly-incremented count straight from the DB).
    """
    User.objects.filter(
        pk=user_id,
        follower_count__gte=CELEBRITY_THRESHOLD,
        is_fanout_on_read=False,
    ).update(is_fanout_on_read=True)
