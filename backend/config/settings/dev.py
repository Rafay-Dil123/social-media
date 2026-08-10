"""Development settings."""
from .base import *  # noqa: F401,F403

DEBUG = True

# Be permissive with hosts in local development.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Browsable API is convenient during development.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
