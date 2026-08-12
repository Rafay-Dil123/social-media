"""Shared pagination.

Cursor (keyset) pagination is used for timelines: it is stable under inserts
(new posts arriving mid-scroll don't shift pages) and stays fast at any depth,
unlike ``LIMIT/OFFSET``.
"""
from __future__ import annotations

from rest_framework.pagination import CursorPagination


class TimelineCursorPagination(CursorPagination):
    page_size = 20
    max_page_size = 50
    ordering = "-created_at"
    cursor_query_param = "cursor"
    page_size_query_param = "limit"
