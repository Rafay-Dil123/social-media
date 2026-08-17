"""
Base settings shared across all environments.

Environment-specific overrides live in ``dev.py`` and ``prod.py``.
Secrets and tunables are read from environment variables (see ``.env.example``).
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# base.py -> settings -> config -> BASE_DIR (backend/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        raise RuntimeError(f"Required environment variable '{key}' is not set.")
    return value


def env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, default))


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

AUTH_USER_MODEL = "accounts.User"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    # Local
    "apps.common",
    "apps.accounts",
    "apps.profiles",
    "apps.follows",
    "apps.posts",
    "apps.interactions",
    "apps.feed",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------------
# Database (PostgreSQL)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "postgres"),
        "USER": env("POSTGRES_USER", "muhammadrafay"),
        "PASSWORD": env("your_password", "your_password"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}


# ---------------------------------------------------------------------------
# Password hashing — Argon2 first (recommended by OWASP).
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth_register": "10/hour",
        "auth_login": "10/min",
        "auth_refresh": "60/min",
    },
    "EXCEPTION_HANDLER": "apps.accounts.exceptions.api_exception_handler",
}


# ---------------------------------------------------------------------------
# Auth / token configuration (consumed by accounts.services)
# ---------------------------------------------------------------------------
AUTH_TOKENS = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("ACCESS_TOKEN_LIFETIME_MINUTES", 15)),
    "REFRESH_SLIDING_LIFETIME": timedelta(days=env_int("REFRESH_SLIDING_DAYS", 30)),
    "REFRESH_ABSOLUTE_LIFETIME": timedelta(days=env_int("REFRESH_ABSOLUTE_DAYS", 90)),
    "ALGORITHM": "HS256",
    "ISSUER": "social-media-api",
}

REFRESH_COOKIE = {
    "NAME": env("REFRESH_COOKIE_NAME", "refresh_token"),
    "SECURE": env_bool("REFRESH_COOKIE_SECURE", False),
    "SAMESITE": env("REFRESH_COOKIE_SAMESITE", "Lax"),
    "HTTPONLY": True,
    # Scope the cookie to the auth routes so it is only sent where needed.
    "PATH": "/api/v1/auth",
}


# ---------------------------------------------------------------------------
# CORS — allow the React dev server, with credentials for the refresh cookie.
# ---------------------------------------------------------------------------
FRONTEND_ORIGIN = env("FRONTEND_ORIGIN", "http://localhost:5173")
CORS_ALLOWED_ORIGINS = [FRONTEND_ORIGIN]
CORS_ALLOW_CREDENTIALS = True


# ---------------------------------------------------------------------------
# Media / object storage (Phase 2 — presigned direct-to-bucket uploads)
# ---------------------------------------------------------------------------
# Public base for building media URLs from a stored key at read time.
MEDIA_CDN_BASE = env("MEDIA_CDN_BASE", "http://localhost:9000/media")

AWS_S3_BUCKET = env("AWS_S3_BUCKET", "media")
AWS_S3_REGION = env("AWS_S3_REGION", "us-east-1")
# Set for MinIO/LocalStack (e.g. http://localhost:9000); blank uses real AWS.
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", "")

MEDIA_UPLOAD = {
    "PRESIGN_EXPIRY_SECONDS": env_int("MEDIA_PRESIGN_EXPIRY", 300),
    "MAX_IMAGE_BYTES": env_int("MEDIA_MAX_IMAGE_BYTES", 15 * 1024 * 1024),
    "MAX_VIDEO_BYTES": env_int("MEDIA_MAX_VIDEO_BYTES", 300 * 1024 * 1024),
    # content_type -> (kind, extension)
    "ALLOWED_TYPES": {
        "image/jpeg": ("image", "jpg"),
        "image/png": ("image", "png"),
        "image/webp": ("image", "webp"),
        "video/mp4": ("video", "mp4"),
    },
}


# ---------------------------------------------------------------------------
# Redis (Phase 3 — live counters; Phase 5/6 — feed + post cache)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

# Feed fan-out (Phase 5)
FEED = {
    "MAX_LEN": env_int("FEED_MAX_LEN", 800),        # entries kept per user ZSET
    "READ_PAGE": env_int("FEED_READ_PAGE", 20),
    "CELEB_WINDOW_HOURS": env_int("FEED_CELEB_WINDOW_HOURS", 48),
    "FANOUT_BATCH": env_int("FEED_FANOUT_BATCH", 5000),
    "BACKFILL_LIMIT": env_int("FEED_BACKFILL_LIMIT", 50),
    "EMPTY_MARKER_TTL": env_int("FEED_EMPTY_MARKER_TTL", 300),
}

# Post hydration cache (Phase 6)
POST_CACHE = {
    "TTL_SECONDS": env_int("POST_CACHE_TTL", 600),
    "TTL_JITTER": env_int("POST_CACHE_TTL_JITTER", 120),
    "LOCK_TTL": env_int("POST_CACHE_LOCK_TTL", 5),
    "NEG_TTL": env_int("POST_CACHE_NEG_TTL", 30),
}


# ---------------------------------------------------------------------------
# Celery (Phase 4 — async workers behind the transactional outbox)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
# Redeliver a job if the worker dies mid-run; jobs must be idempotent.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Run tasks inline (no broker/worker) when set — handy for local dev and tests.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
