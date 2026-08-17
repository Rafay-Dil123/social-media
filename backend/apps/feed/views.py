"""Home feed endpoint: hybrid selection (Phase 5) + hydration (Phase 6)."""
from __future__ import annotations

from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.services import hydrate
from . import selectors


class HomeFeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(
            int(request.query_params.get("limit", settings.FEED["READ_PAGE"])),
            settings.FEED["READ_PAGE"] * 2,
        )
        before_ts = _parse_cursor(request.query_params.get("cursor"))

        entries = selectors.home_feed_entries(request.user, limit, before_ts)
        ids = [pid for pid, _ in entries]
        posts = hydrate.hydrate_posts(ids, viewer=request.user)

        next_cursor = entries[-1][1] if len(entries) == limit else None
        return Response({"results": posts, "next_cursor": next_cursor})


def _parse_cursor(raw):
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
