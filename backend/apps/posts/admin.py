from django.contrib import admin

from .models import Post, PostMedia


class PostMediaInline(admin.TabularInline):
    model = PostMedia
    extra = 0
    fields = ("type", "storage_key", "position", "state")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "visibility", "like_count", "created_at", "deleted_at")
    list_filter = ("visibility",)
    search_fields = ("id", "user__username", "caption")
    readonly_fields = ("created_at", "updated_at")
    inlines = [PostMediaInline]


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "post", "type", "state", "created_at")
    list_filter = ("type", "state")
    search_fields = ("id", "owner__username", "storage_key")
