from rest_framework import serializers

from .models import Profile


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ("display_name", "bio", "avatar_url", "created_at", "updated_at")
        read_only_fields = fields
