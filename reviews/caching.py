"""Low-level cache helpers for the public catalog pages.

Only plain data (repository results, filter options) is cached, never rendered
responses, so language switching and per-user markup keep working normally.
A monotonically increasing version in the cache namespace makes invalidation a
single cheap write: every key built with the previous version becomes unused.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import cache

_VERSION_KEY = "proflens:catalog:version"
_HOME_KEY = "proflens:home"
_OPTIONS_KEY = "proflens:options"
_SCHOOL_KEY = "proflens:school"
_STATS_KEY = "proflens:stats"
_DEFAULT_TIMEOUT = 300


def _timeout() -> int:
    """Return the catalog cache TTL from settings."""
    return int(getattr(settings, "CATALOG_CACHE_TIMEOUT", _DEFAULT_TIMEOUT))


def _version() -> int:
    """Return the current catalog cache version, initializing it if needed."""
    version = cache.get(_VERSION_KEY)
    if version is None:
        version = 1
        cache.add(_VERSION_KEY, version, timeout=None)
    return version


def invalidate_catalog_cache() -> None:
    """Invalidate every cached home/school view built from catalog data."""
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        cache.set(_VERSION_KEY, 1, timeout=None)


def _digest(*parts: str) -> str:
    """Return a compact, cache-safe key fragment for the given values."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def options_cache_key() -> str:
    """Cache key for the school/city/department filter option lists."""
    return f"{_OPTIONS_KEY}:{_version()}"


def home_cache_key(q: str, school: str, city: str, department: str, sort: str) -> str:
    """Cache key for home search results for one filter combination."""
    return f"{_HOME_KEY}:{_version()}:{_digest(q, school, city, department, sort)}"


def school_cache_key(slug: str) -> str:
    """Cache key for a university page's professor list and statistics."""
    return f"{_SCHOOL_KEY}:{_version()}:{_digest(slug)}"


def stats_cache_key(professor_id: str) -> str:
    """Cache key for one professor's aggregate rating statistics."""
    return f"{_STATS_KEY}:{_version()}:{_digest(str(professor_id))}"


def get_or_set(key: str, factory) -> object:
    """Return the cached value at ``key``, computing and storing it on a miss."""
    value = cache.get(key)
    if value is None:
        value = factory()
        cache.set(key, value, timeout=_timeout())
    return value
