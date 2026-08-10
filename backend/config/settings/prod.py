"""Production settings.

Everything security-sensitive is enabled here and driven by environment
variables. This module assumes it is served over HTTPS behind a proxy.
"""
from .base import *  # noqa: F401,F403

DEBUG = False

# ALLOWED_HOSTS must be provided explicitly in production.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405

# JSON only — no browsable API in production.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
]

# ---------------------------------------------------------------------------
# HTTPS / security hardening
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# The refresh cookie must be Secure in production regardless of .env.
REFRESH_COOKIE["SECURE"] = True  # noqa: F405

# Trust the frontend origin for CSRF (form posts / cookie flows).
CSRF_TRUSTED_ORIGINS = [FRONTEND_ORIGIN]  # noqa: F405
