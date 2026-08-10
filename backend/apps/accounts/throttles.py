"""Rate-limit scopes for auth endpoints (brute-force protection).

Rates are configured in ``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``.
For multi-process production, back DRF's cache with Redis.
"""
from rest_framework.throttling import ScopedRateThrottle


class RegisterThrottle(ScopedRateThrottle):
    scope_attr = "throttle_scope"


class LoginThrottle(ScopedRateThrottle):
    scope_attr = "throttle_scope"
