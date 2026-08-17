"""Feed routes, mounted under /api/v1/ by config.urls."""
from django.urls import path

from .views import HomeFeedView

app_name = "feed"

urlpatterns = [
    path("feed/", HomeFeedView.as_view(), name="home-feed"),
]
