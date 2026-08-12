"""Follow routes, mounted under /api/v1/users/ by config.urls."""
from django.urls import path

from .views import FollowView

app_name = "follows"

urlpatterns = [
    path("<uuid:user_id>/follow/", FollowView.as_view(), name="follow"),
]
