from django.contrib import admin

from .models import Like


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "post", "created_at", "deleted_at")
    search_fields = ("user__username", "post__id")
    list_filter = ("deleted_at",)
