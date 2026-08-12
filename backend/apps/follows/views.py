"""Thin follow/unfollow endpoints."""
from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class FollowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        services.follow(request.user, user_id)
        return Response(status=204)

    def delete(self, request, user_id):
        services.unfollow(request.user, user_id)
        return Response(status=204)
