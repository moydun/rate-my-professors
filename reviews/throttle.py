"""Tiny cache-backed rate limiter used to slow down abusive bursts.

Limits are fixed-window counters stored in the Django cache, so no extra
dependency is needed.  A multi-process deployment must use a shared cache
backend (Redis/Memcached) for the limit to be global; the locmem default is
fine for a single process and for tests.

Authenticated requests are keyed by username, anonymous ones by client IP, so
one user cannot exhaust another user's budget.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _

_CACHE_PREFIX = "ratelimit"

View = Callable[..., HttpResponse]


def _client_ip(request: HttpRequest) -> str:
    """Best-effort client IP, honouring the first ``X-Forwarded-For`` hop."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _subject(request: HttpRequest, user: Any = None) -> str:
    """Return the identity the counter is attached to.

    Anonymous traffic is keyed by client IP. Authenticated traffic combines
    the username with the client IP, so one user on several networks does not
    share a single budget while distinct users stay independent.
    """
    ip = _client_ip(request)
    if user is None:
        user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.get_username()}|ip:{ip}"
    return f"ip:{ip}"


def _config(action: str) -> tuple[bool, int, int]:
    """Return ``(enabled, limit, window)`` for ``action`` from settings."""
    if not getattr(settings, "RATELIMIT_ENABLE", True):
        return False, 0, 0
    limit, window = getattr(settings, "RATELIMIT", {}).get(action, (0, 0))
    return True, limit, window


def is_rate_limited(request: HttpRequest, action: str, user: Any = None) -> bool:
    """Return ``True`` when ``action`` exceeded its limit for this subject."""
    enabled, limit, window = _config(action)
    if not enabled:
        return False
    key = f"{_CACHE_PREFIX}:{action}:{_subject(request, user)}"
    if cache.add(key, 1, timeout=window):
        return False
    try:
        hits = cache.incr(key)
    except ValueError:
        # Counter expired between add and incr; start a fresh window.
        cache.set(key, 1, timeout=window)
        return False
    return hits > limit


def rate_limit(action: str) -> Callable[[View], View]:
    """Decorator that answers ``429`` once ``action``'s budget is spent."""

    def decorator(view: View) -> View:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.method == "POST" and is_rate_limited(request, action):
                return HttpResponse(
                    _("Слишком много запросов. Попробуйте позже."), status=429
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
