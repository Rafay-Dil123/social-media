"""Root URL configuration."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/users/", include("apps.follows.urls")),
    path("api/v1/", include("apps.posts.urls")),
    path("api/v1/", include("apps.interactions.urls")),
]
