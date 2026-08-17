from django.apps import AppConfig


class FeedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.feed"
    label = "feed"
    verbose_name = "Home feed"

    def ready(self) -> None:
        # Register the fan-out handler for the post.created outbox event and
        # connect the backfill signal. Done here so wiring lives with the app.
        from apps.common import relay
        from apps.follows.signals import user_followed
        from . import signals as feed_signals
        from . import tasks

        relay.register("post.created", tasks.fanout_post)
        user_followed.connect(
            feed_signals.on_user_followed, dispatch_uid="feed.backfill_on_follow"
        )
