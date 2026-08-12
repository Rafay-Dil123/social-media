"""Thin media-upload endpoints (Phase 2)."""
from __future__ import annotations

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import media as media_service


class UploadInitSerializer(serializers.Serializer):
    content_type = serializers.CharField()
    size = serializers.IntegerField(min_value=1)


class UploadInitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = UploadInitSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        result = media_service.init_upload(
            request.user,
            content_type=s.validated_data["content_type"],
            declared_size=s.validated_data["size"],
        )
        return Response(result, status=201)


class UploadConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, upload_id):
        media = media_service.confirm_upload(request.user, upload_id)
        return Response(
            {"upload_id": media.id, "state": media.get_state_display()}, status=200
        )
