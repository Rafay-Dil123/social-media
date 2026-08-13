"""Interaction routes, mounted under /api/v1/ by config.urls."""
from django.urls import path

from .views import LikeView

app_name = "interactions"

urlpatterns = [
    path("posts/<int:post_id>/like/", LikeView.as_view(), name="like"),
]
