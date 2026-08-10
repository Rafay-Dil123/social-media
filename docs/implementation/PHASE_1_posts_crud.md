# Phase 1 — Posts CRUD (synchronous core)

**Goal.** Create / read / delete posts and media, correct and simple, on
Postgres alone. The feed is a plain query (fan-out on read) — we replace it in
Phase 5. **This phase is shippable.**

**New app:** `apps.posts`.

---

## 1.1 Models — `apps/posts/models.py`

```python
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Post(TimeStampedModel):
    # BigAutoField, not UUID: high-volume, want sequential index locality.
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts"
    )

    caption = models.TextField(blank=True)                     # text-only allowed

    class Visibility(models.IntegerChoices):
        PUBLIC = 0, "public"
        FOLLOWERS = 1, "followers"
        PRIVATE = 2, "private"

    visibility = models.SmallIntegerField(
        choices=Visibility.choices, default=Visibility.PUBLIC
    )
    media_preview = models.JSONField(null=True, blank=True)    # first thumbnail, denorm
    extra = models.JSONField(default=dict, blank=True)         # sparse metadata

    like_count = models.BigIntegerField(default=0)             # mirror of Redis (Phase 3)
    comment_count = models.BigIntegerField(default=0)

    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "posts"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_post_user_created"),
            models.Index(fields=["-created_at", "-id"], name="idx_post_created"),
        ]
        constraints = [
            # optional public id later; for now the bigint PK is internal.
        ]

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class PostMedia(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="media", null=True
    )  # null until attached (upload happens before the post exists — Phase 2)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media"
    )

    class Type(models.IntegerChoices):
        IMAGE = 0, "image"
        VIDEO = 1, "video"

    type = models.SmallIntegerField(choices=Type.choices)
    storage_key = models.TextField()                           # bucket path, NOT a URL
    position = models.SmallIntegerField(default=0)             # carousel order

    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)

    class State(models.IntegerChoices):
        PENDING = 0, "pending"
        READY = 1, "ready"
        FAILED = 2, "failed"

    state = models.SmallIntegerField(choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "post_media"
        indexes = [models.Index(fields=["post", "position"])]
```

> **Manager for soft delete.** Add a default manager that hides deleted rows so
> you don't sprinkle `deleted_at__isnull=True` everywhere:
> ```python
> class PostQuerySet(models.QuerySet):
>     def alive(self): return self.filter(deleted_at__isnull=True)
> objects = PostQuerySet.as_manager()
> ```
> Keep an `all_objects` manager for admin/moderation that sees everything.

---

## 1.2 Serializers — `apps/posts/serializers.py`

One per direction (repo convention):

```python
from __future__ import annotations

from rest_framework import serializers
from .models import Post, PostMedia


class PostCreateSerializer(serializers.Serializer):
    caption = serializers.CharField(allow_blank=True, max_length=2200)
    visibility = serializers.ChoiceField(choices=Post.Visibility.choices,
                                         default=Post.Visibility.PUBLIC)
    media_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=10
    )

    def validate(self, attrs):
        if not attrs["caption"].strip() and not attrs["media_ids"]:
            raise serializers.ValidationError("A post needs a caption or media.")
        return attrs


class MediaReadSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = ("id", "type", "url", "position", "width", "height", "duration_ms")

    def get_url(self, obj) -> str:
        # Build CDN URL from the stored key at read time.
        from django.conf import settings
        return f"{settings.MEDIA_CDN_BASE}/{obj.storage_key}"


class PostReadSerializer(serializers.ModelSerializer):
    media = MediaReadSerializer(many=True, read_only=True)
    author = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ("id", "caption", "visibility", "author", "media",
                  "like_count", "comment_count", "created_at")

    def get_author(self, obj):
        u = obj.user
        return {"id": str(u.id), "username": u.username,
                "avatar_url": getattr(u.profile, "avatar_url", "")}
```

> In Phase 6 the read shape is produced by the **hydration service** from cache,
> not by re-serializing ORM objects per request. `PostReadSerializer` stays as
> the canonical shape / for the non-cached path.

---

## 1.3 Services (writes) — `apps/posts/services.py`

```python
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import NotFound, PermissionDenied
from .models import Post, PostMedia


@transaction.atomic
def create_post(user, *, caption: str, visibility: int, media_ids: list[int]) -> Post:
    post = Post.objects.create(user=user, caption=caption, visibility=visibility)

    if media_ids:
        # Attach only this user's READY media that isn't already attached.
        media = list(
            PostMedia.objects.select_for_update().filter(
                id__in=media_ids, owner=user, post__isnull=True,
                state=PostMedia.State.READY,
            )
        )
        if len(media) != len(media_ids):
            raise PermissionDenied("Some media is missing, not yours, or not ready.")
        for i, m in enumerate(sorted(media, key=lambda m: media_ids.index(m.id))):
            m.post = post
            m.position = i
        PostMedia.objects.bulk_update(media, ["post", "position"])
        post.media_preview = _preview_from(media[0])
        post.save(update_fields=["media_preview"])

    # Phase 4 adds: Outbox.objects.create(event_type="post.created", payload=...)
    return post


def delete_post(user, post_id: int) -> None:
    post = Post.objects.alive().filter(pk=post_id).first()
    if post is None:
        raise NotFound("Post not found.")
    if post.user_id != user.id:
        raise PermissionDenied()
    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at"])
    # Phase 6 adds: cache_evict(f"post:{post_id}")


def _preview_from(m: PostMedia) -> dict:
    return {"type": m.type, "key": m.storage_key, "w": m.width, "h": m.height}
```

> **Gotcha — media ordering.** Preserve the client's `media_ids` order for the
> carousel; don't rely on DB return order (hence the `sorted(...index...)`).

---

## 1.4 Selectors (reads) — `apps/posts/selectors.py`

```python
from __future__ import annotations

from django.db.models import Q
from apps.follows.selectors import followee_ids
from .models import Post


def get_post(viewer, post_id: int) -> Post | None:
    post = (Post.objects.alive()
            .select_related("user", "user__profile")
            .prefetch_related("media")
            .filter(pk=post_id).first())
    if post and _can_view(viewer, post):
        return post
    return None


def list_user_posts(viewer, author_id):
    qs = (Post.objects.alive()
          .select_related("user", "user__profile").prefetch_related("media")
          .filter(user_id=author_id).order_by("-created_at", "-id"))
    return _visibility_filter(qs, viewer)


def naive_home_feed(viewer):
    """Fan-out-on-read baseline. Also the rebuild query used by Phase 5."""
    ids = followee_ids(viewer.id)
    qs = (Post.objects.alive()
          .select_related("user", "user__profile").prefetch_related("media")
          .filter(user_id__in=ids).order_by("-created_at", "-id"))
    return qs


def _can_view(viewer, post) -> bool:
    if post.visibility == Post.Visibility.PUBLIC:
        return True
    if viewer.is_anonymous:
        return False
    if post.user_id == viewer.id:
        return True
    if post.visibility == Post.Visibility.FOLLOWERS:
        return post.user_id in set(followee_ids(viewer.id))
    return False


def _visibility_filter(qs, viewer):
    if viewer.is_anonymous:
        return qs.filter(visibility=Post.Visibility.PUBLIC)
    return qs  # refine per relationship as needed
```

> **N+1 guard.** Always `select_related("user","user__profile")` +
> `prefetch_related("media")`. Add a test that asserts a feed page runs a bounded
> number of queries (`django.test.utils.CaptureQueriesContext`).

---

## 1.5 Views / URLs

```python
# apps/posts/views.py — thin
class PostListCreateView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def post(self, request):
        s = PostCreateSerializer(data=request.data); s.is_valid(raise_exception=True)
        post = services.create_post(request.user, **s.validated_data)
        return Response(PostReadSerializer(post, context={"request": request}).data,
                        status=201)


class PostDetailView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, post_id):
        post = selectors.get_post(request.user, post_id)
        if not post:
            raise NotFound()
        return Response(PostReadSerializer(post, context={"request": request}).data)

    def delete(self, request, post_id):
        services.delete_post(request.user, post_id)
        return Response(status=204)


class HomeFeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = selectors.naive_home_feed(request.user)
        page = TimelineCursorPagination()
        rows = page.paginate_queryset(qs, request)
        data = PostReadSerializer(rows, many=True, context={"request": request}).data
        return page.get_paginated_response(data)
```

```python
# apps/posts/urls.py  (mounted at /api/v1/)
urlpatterns = [
    path("posts/", PostListCreateView.as_view()),
    path("posts/<int:post_id>/", PostDetailView.as_view()),
    path("feed/", HomeFeedView.as_view()),
]
```

---

## 1.6 Tests (`apps/posts/tests/`)

- `test_create_text_only_post`
- `test_create_post_with_media_orders_carousel`
- `test_create_rejects_unready_or_foreign_media`
- `test_delete_soft_deletes_and_hides_from_reads`
- `test_delete_requires_owner` (403)
- `test_visibility_followers_only_hidden_from_stranger`
- `test_feed_returns_followees_posts_newest_first`
- `test_feed_query_count_is_bounded` (no N+1)

---

## Definition of done

You can create text/media posts, read them with visibility rules, soft-delete
them, and load a chronological home feed — all on Postgres, all tested. Ship it.
Phases 2–7 scale this exact behavior.
