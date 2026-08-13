from django.apps import AppConfig
from apps.common import relay
from . import tasks


class PostsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.posts"
    label = "posts"
    verbose_name = "Posts & media"

    def ready(self) -> None:
        relay.register("post.created", tasks.on_post_created)
