"""Thin post endpoints: parse input, call a service/selector, return."""
from __future__ import annotations

from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import NotFound
from apps.common.pagination import TimelineCursorPagination
from . import selectors, services
from .serializers import PostCreateSerializer, PostReadSerializer
from .services import hydrate


class PostListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PostCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = services.create_post(request.user, **serializer.validated_data)
        # Re-fetch through the selector so media/author are prefetched for output.
        post = selectors.get_post(request.user, post.id)
        return Response(
            PostReadSerializer(post, context={"request": request}).data, status=201
        )


class PostDetailView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, post_id):
        # Enforce visibility, then serve from the cache (single-flight) with
        # live counts merged in.
        post = selectors.get_post(request.user, post_id)
        if post is None:
            raise NotFound("Post not found.")
        data = hydrate.hydrate_single(post_id)
        if data is None:
            raise NotFound("Post not found.")
        return Response(data)

    def delete(self, request, post_id):
        services.delete_post(request.user, post_id)
        return Response(status=204)


class UserPostsView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, user_id):
        qs = selectors.list_user_posts(request.user, user_id)
        return _paginate(qs, request)


def _paginate(qs, request):
    paginator = TimelineCursorPagination()
    page = paginator.paginate_queryset(qs, request)
    data = PostReadSerializer(page, many=True, context={"request": request}).data
    return paginator.get_paginated_response(data)
