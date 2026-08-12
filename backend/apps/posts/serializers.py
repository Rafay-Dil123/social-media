"""Post serializers — one per direction (create vs read)."""
from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from .models import Post, PostMedia


class PostCreateSerializer(serializers.Serializer):
    caption = serializers.CharField(allow_blank=True, required=False, default="",
                                    max_length=2200, trim_whitespace=False)
    visibility = serializers.ChoiceField(
        choices=Post.Visibility.choices, default=Post.Visibility.PUBLIC
    )
    media_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=10
    )

    def validate(self, attrs):
        if not attrs.get("caption", "").strip() and not attrs.get("media_ids"):
            raise serializers.ValidationError("A post needs a caption or media.")
        return attrs


class MediaReadSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    type = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = PostMedia
        fields = ("id", "type", "url", "position", "width", "height", "duration_ms")

    def get_url(self, obj) -> str:
        base = settings.MEDIA_CDN_BASE.rstrip("/")
        return f"{base}/{obj.storage_key}"


class AuthorSerializer(serializers.Serializer):
    id = serializers.CharField()
    username = serializers.CharField()
    avatar_url = serializers.CharField(allow_blank=True)


class PostReadSerializer(serializers.ModelSerializer):
    media = MediaReadSerializer(many=True, read_only=True)
    visibility = serializers.CharField(source="get_visibility_display", read_only=True)
    author = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id", "caption", "visibility", "author", "media",
            "like_count", "comment_count", "created_at",
        )

    def get_author(self, obj) -> dict:
        user = obj.user
        avatar = getattr(getattr(user, "profile", None), "avatar_url", "") or ""
        return {"id": str(user.id), "username": user.username, "avatar_url": avatar}
