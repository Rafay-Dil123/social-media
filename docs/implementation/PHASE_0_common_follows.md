# Phase 0 — Common foundations + Follow graph

**Goal.** The shared plumbing every later phase needs, plus the social graph the
feed is built on. No Redis, no async yet.

**New apps:** `apps.follows`. **Touched:** `apps.common`.

---

## 0.1 `common` additions

### `apps/common/pagination.py` — cursor pagination

Offset pagination (`LIMIT n OFFSET m`) degrades on deep feeds and skips/duplicates
rows when new posts arrive mid-scroll. Use keyset/cursor pagination ordered by
`(created_at, id)`.

```python
from __future__ import annotations

from rest_framework.pagination import CursorPagination


class TimelineCursorPagination(CursorPagination):
    page_size = 20
    max_page_size = 50
    ordering = "-created_at"          # tie-break with id via the model Meta
    cursor_query_param = "cursor"
    page_size_query_param = "limit"
```

### `apps/common/permissions.py`

```python
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOwnerOrReadOnly(BasePermission):
    """Read for anyone; write only for the object's owner (obj.user_id)."""

    def has_object_permission(self, request, view, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return getattr(obj, "user_id", None) == request.user.id
```

### Move the error envelope to `common`

Relocate `accounts/exceptions.py::api_exception_handler` → `common/exceptions.py`
and update `REST_FRAMEWORK["EXCEPTION_HANDLER"]` to
`"apps.common.exceptions.api_exception_handler"`. Keep the auth-specific
exception classes in `accounts`. Add a couple of shared ones:

```python
class NotFound(APIException):
    status_code = 404
    default_code = "not_found"
    default_detail = "Not found."

class PermissionDenied(APIException):
    status_code = 403
    default_code = "permission_denied"
    default_detail = "You do not have permission to perform this action."
```

---

## 0.2 `follows` app

### `apps/follows/models.py`

```python
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class Follow(UUIDModel):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following_set"
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follower_set"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "follows"
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"], name="uniq_follow_pair"
            ),
            models.CheckConstraint(
                check=~models.Q(follower=models.F("following")), name="no_self_follow"
            ),
        ]
        indexes = [
            models.Index(fields=["follower", "created_at"]),   # who I follow
            models.Index(fields=["following", "created_at"]),  # my followers
        ]
```

### Follower counts + celebrity flag

The feed (Phase 5) needs both. Add to the `User` model (or a 1:1
`UserStats` row to keep the hot-updated columns off the user table):

```python
# apps/accounts/models.py (User) — or a separate accounts.UserStats
follower_count = models.BigIntegerField(default=0)
following_count = models.BigIntegerField(default=0)
is_fanout_on_read = models.BooleanField(default=False)   # celebrity flag
```

Keep them correct on follow/unfollow inside the same transaction, and flip the
flag past a threshold:

```python
# apps/follows/services.py
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.accounts.models import User
from apps.common.exceptions import ValidationError
from .models import Follow

CELEBRITY_THRESHOLD = 100_000


@transaction.atomic
def follow(follower: User, following_id) -> Follow:
    if follower.id == following_id:
        raise ValidationError("You cannot follow yourself.")
    try:
        f = Follow.objects.create(follower=follower, following_id=following_id)
    except IntegrityError:
        # already following — idempotent
        return Follow.objects.get(follower=follower, following_id=following_id)

    User.objects.filter(pk=following_id).update(follower_count=F("follower_count") + 1)
    User.objects.filter(pk=follower.id).update(following_count=F("following_count") + 1)
    _maybe_flip_celebrity(following_id)
    return f


@transaction.atomic
def unfollow(follower: User, following_id) -> None:
    deleted, _ = Follow.objects.filter(
        follower=follower, following_id=following_id
    ).delete()
    if deleted:
        User.objects.filter(pk=following_id).update(follower_count=F("follower_count") - 1)
        User.objects.filter(pk=follower.id).update(following_count=F("following_count") - 1)


def _maybe_flip_celebrity(user_id) -> None:
    User.objects.filter(pk=user_id, follower_count__gte=CELEBRITY_THRESHOLD,
                        is_fanout_on_read=False).update(is_fanout_on_read=True)
```

> **Gotcha — count drift.** `F()` updates are atomic and race-safe. But if you
> ever bulk-delete follows, reconcile counts with a periodic job
> (`COUNT(*)`), because bulk paths bypass the per-row `+1/-1`.

### `apps/follows/selectors.py`

Other apps must call these, never query `Follow` directly (domain isolation).

```python
from __future__ import annotations

from .models import Follow


def followee_ids(user_id) -> list:
    return list(
        Follow.objects.filter(follower_id=user_id).values_list("following_id", flat=True)
    )


def celebrity_followee_ids(user_id) -> list:
    return list(
        Follow.objects.filter(
            follower_id=user_id, following__is_fanout_on_read=True
        ).values_list("following_id", flat=True)
    )


def follower_ids(user_id, *, active_only=False):
    qs = Follow.objects.filter(following_id=user_id)
    return qs.values_list("follower_id", flat=True).iterator(chunk_size=5000)
```

> **Gotcha — big fan-out.** `follower_ids` returns an **iterator** (chunked), not
> a list — a celebrity has millions of followers and you must never
> `list()` them all into memory. Phase 5's worker consumes this in batches.

### Views / URLs

Thin views over the services:

```python
# apps/follows/views.py
class FollowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        services.follow(request.user, user_id)
        return Response(status=204)

    def delete(self, request, user_id):
        services.unfollow(request.user, user_id)
        return Response(status=204)
```

```python
# apps/follows/urls.py — mounted at /api/v1/users/
urlpatterns = [
    path("<uuid:user_id>/follow/", FollowView.as_view(), name="follow"),
]
```

Add `path("api/v1/users/", include("apps.follows.urls"))` to `config/urls.py`.

---

## 0.3 Tests (`apps/follows/tests/`)

- `test_follow_creates_row_and_bumps_counts`
- `test_follow_is_idempotent` (double follow → one row, count +1 once)
- `test_cannot_self_follow` (check constraint + service guard)
- `test_unfollow_decrements`
- `test_celebrity_flag_flips_past_threshold`
- `test_followee_ids_selector`

---

## Definition of done

Follow/unfollow endpoints work, counts stay correct, the celebrity flag flips,
selectors are the only cross-app entry point, and `common` has cursor
pagination + shared permissions + the relocated error envelope.
