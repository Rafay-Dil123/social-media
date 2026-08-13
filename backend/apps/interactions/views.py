"""Thin like/unlike endpoints."""
from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class LikeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, post_id):
        services.like(request.user, post_id)
        return Response({"like_count": services.like_count(post_id)}, status=200)

    def delete(self, request, post_id):
        services.unlike(request.user, post_id)
        return Response({"like_count": services.like_count(post_id)}, status=200)
