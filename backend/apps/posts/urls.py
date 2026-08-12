"""Post + media routes, mounted under /api/v1/ by config.urls."""
from django.urls import path

from .media_views import UploadConfirmView, UploadInitView
from .views import (
    HomeFeedView,
    PostDetailView,
    PostListCreateView,
    UserPostsView,
)

app_name = "posts"

urlpatterns = [
    path("posts/", PostListCreateView.as_view(), name="post-list-create"),
    path("posts/<int:post_id>/", PostDetailView.as_view(), name="post-detail"),
    path("users/<uuid:user_id>/posts/", UserPostsView.as_view(), name="user-posts"),
    path("feed/", HomeFeedView.as_view(), name="home-feed"),
    # media
    path("media/upload-init/", UploadInitView.as_view(), name="media-upload-init"),
    path("media/<int:upload_id>/confirm/", UploadConfirmView.as_view(),
         name="media-confirm"),
]
