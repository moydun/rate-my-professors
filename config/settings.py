import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

from reviews.constants import RATE_LIMITS

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Minimal stdlib .env loader so local dev works without extra deps.

    Existing environment variables always win so real deployments can override
    the file. Missing files are ignored.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")

# Django's test command runs with DEBUG off by default; force a safe dev mode so
# `manage.py test` never requires production secrets or strict HTTPS flags.
_is_test = sys.argv[1:2] == ["test"]

DEFAULT_SECRET_KEY = "dev-only-change-me"
# Used only while running the test suite; never valid for production boot.
TEST_SECRET_KEY = "test-secret-key-for-ci-only-must-not-be-used-in-production-0123456789"
# Safe fallback that works for local development without exposing the app to
# Host-header attacks the way a bare "*" would.
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
TEST_ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]


def _parse_bool(value, default=False):
    """Parse common truthy/falsy strings; fall back to `default` when unset."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_debug(is_test, raw):
    """DEBUG comes from the environment, defaulting to False (prod-safe)."""
    if is_test:
        return True
    return _parse_bool(raw, default=False)


def _get_allowed_hosts(raw, is_test=False):
    """Parse ALLOWED_HOSTS, falling back to safe localhost-only defaults."""
    hosts = [h.strip() for h in (raw or "").split(",") if h.strip()]
    if hosts:
        return hosts
    return list(TEST_ALLOWED_HOSTS if is_test else DEFAULT_ALLOWED_HOSTS)


def _get_csrf_trusted_origins(raw):
    """Parse CSRF_TRUSTED_ORIGINS (comma-separated origins); empty by default.

    An empty list is the Django default and keeps local HTTP dev working, so
    an unset variable never breaks development; deployments behind HTTPS set
    the variable explicitly.
    """
    return [o.strip() for o in (raw or "").split(",") if o.strip()]


DEBUG = _get_debug(_is_test, os.environ.get("DEBUG"))
if _is_test:
    SECRET_KEY = os.environ.get("SECRET_KEY") or TEST_SECRET_KEY
else:
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEFAULT_SECRET_KEY
ALLOWED_HOSTS = _get_allowed_hosts(os.environ.get("ALLOWED_HOSTS"), _is_test)
# Trusted origins for CSRF come from the environment, same as SECRET_KEY and
# ALLOWED_HOSTS above; they are required for HTTPS deployments behind a proxy.
CSRF_TRUSTED_ORIGINS = _get_csrf_trusted_origins(os.environ.get("CSRF_TRUSTED_ORIGINS"))


def _validate_secret_key(secret_key, debug):
    """Refuse to boot in production with the shipped placeholder key."""
    if not debug and secret_key == DEFAULT_SECRET_KEY:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set to a unique, unpredictable value when DEBUG=False."
        )


def _validate_allowed_hosts(hosts, debug):
    """Wildcard hosts are a dev convenience only; reject them in production."""
    if not debug and "*" in hosts:
        raise ImproperlyConfigured("ALLOWED_HOSTS must not contain '*' when DEBUG=False.")


def _get_secure_cookie(raw, default):
    """Resolve a secure-cookie flag from the environment.

    An unset variable falls back to ``default`` (``not DEBUG``), so secure
    cookies stay on in production while remaining explicitly overridable by
    ``SESSION_COOKIE_SECURE`` / ``CSRF_COOKIE_SECURE``.
    """
    if raw is None:
        return default
    return _parse_bool(raw, default=default)


def _get_bool_setting(name, default):
    """Read a boolean setting from the environment, falling back to `default`."""
    return _parse_bool(os.environ.get(name), default=default)


def _get_int_setting(name, default):
    """Read an integer setting from the environment, falling back to `default`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _security_settings(
    debug,
    session_secure=None,
    csrf_secure=None,
    ssl_redirect=None,
    hsts_seconds=None,
    hsts_include_subdomains=None,
    hsts_preload=None,
    content_type_nosniff=None,
):
    """HTTPS/cookie hardening switches, enabled only when DEBUG=False.

    The optional arguments let the environment override the debug-derived
    defaults; when left as ``None`` each switch tracks ``debug`` (secure in
    production, relaxed for local dev and the test suite).  Kept as a pure
    function so the production branch can be unit-tested without mutating the
    process environment.
    """
    return {
        "SECURE_SSL_REDIRECT": not debug if ssl_redirect is None else ssl_redirect,
        "SECURE_HSTS_SECONDS": (0 if debug else 31536000) if hsts_seconds is None else hsts_seconds,
        "SECURE_HSTS_INCLUDE_SUBDOMAINS": (
            not debug if hsts_include_subdomains is None else hsts_include_subdomains
        ),
        "SECURE_HSTS_PRELOAD": not debug if hsts_preload is None else hsts_preload,
        "SESSION_COOKIE_SECURE": not debug if session_secure is None else session_secure,
        "CSRF_COOKIE_SECURE": not debug if csrf_secure is None else csrf_secure,
        "SECURE_CONTENT_TYPE_NOSNIFF": (
            True if content_type_nosniff is None else content_type_nosniff
        ),
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https") if not debug else None,
    }


_validate_secret_key(SECRET_KEY, DEBUG)
_validate_allowed_hosts(ALLOWED_HOSTS, DEBUG)
globals().update(
    _security_settings(
        DEBUG,
        session_secure=_get_secure_cookie(os.environ.get("SESSION_COOKIE_SECURE"), not DEBUG),
        csrf_secure=_get_secure_cookie(os.environ.get("CSRF_COOKIE_SECURE"), not DEBUG),
        ssl_redirect=_get_bool_setting("SECURE_SSL_REDIRECT", not DEBUG),
        hsts_seconds=_get_int_setting("SECURE_HSTS_SECONDS", 0 if DEBUG else 31536000),
        hsts_include_subdomains=_get_bool_setting("SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG),
        hsts_preload=_get_bool_setting("SECURE_HSTS_PRELOAD", not DEBUG),
        content_type_nosniff=_get_bool_setting("SECURE_CONTENT_TYPE_NOSNIFF", True),
    )
)

# Clickjacking protection is safe to enable in every environment.
X_FRAME_OPTIONS = os.environ.get("X_FRAME_OPTIONS", "DENY")

# Limit request-body and in-memory file upload sizes to 2.5 MiB.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440

# Referrer-Policy limits how much of our URLs leak to third parties; "same-origin"
# is a safe, privacy-preserving default that can be tightened per deployment.
SECURE_REFERRER_POLICY = os.environ.get("SECURE_REFERRER_POLICY", "same-origin")

# ProfLens does not use these browser features, so disable them by default.
PERMISSIONS_POLICY = "camera=(), geolocation=(), microphone=()"

# Minimal Content-Security-Policy: only same-origin resources are allowed and
# objects/framing/base-uri/form targets are locked down. Inline styles remain
# permitted for template-rendered widths; an empty dict disables the header.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "form-action": [CSP.SELF],
}

# Cookies must never be readable from JavaScript and stay same-site by default.
SESSION_COOKIE_HTTPONLY = _get_bool_setting("SESSION_COOKIE_HTTPONLY", True)
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")

# Password strength requirements enforced by Django auth / password reset.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "reviews.apps.ReviewsConfig",
]
MIDDLEWARE = [
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.http.ConditionalGetMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "reviews.middleware.PermissionsPolicyMiddleware",
    "reviews.middleware.PrivateCacheControlMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "django.template.context_processors.i18n",
    ]},
}]

# Database: DATABASE_URL for Postgres, fallback to SQLite for local dev / tests
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    # simple parse: postgres://user:pass@host:port/dbname  -> use psycopg
    # we rely on dj-database-url style but keep stdlib only
    from urllib.parse import urlparse
    u = urlparse(_db_url)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": u.path.lstrip("/"),
        "USER": u.username or "",
        "PASSWORD": u.password or "",
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

STATIC_URL = "static/"
USE_I18N = True
LANGUAGE_CODE = "ru"
LANGUAGES = [
    ("ru", "Русский"),
    ("ky", "Кыргызча"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Bishkek"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# Rate limiting: simple fixed-window counters kept in the Django cache.
# Disabled by default while running the test suite so unrelated tests cannot
# exhaust a shared per-user/per-IP budget; the throttle tests re-enable it.
RATELIMIT_ENABLE = _parse_bool(os.environ.get("RATELIMIT_ENABLE"), default=not _is_test)
# action -> (max hits, window in seconds); the defaults live in reviews.constants.
RATELIMIT = dict(RATE_LIMITS)

# Shared cache used by the rate limiter (and available for future use). A real
# multi-process deployment should point CACHE_BACKEND at Redis/Memcached so the
# limit is enforced globally instead of per worker.
CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": os.environ.get("CACHE_LOCATION", "proflens"),
    }
}

# Catalog pages (home search, university lists and filter options) are cached
# with django.core.cache; reviews/professors/schools invalidate the namespace.
CATALOG_CACHE_TIMEOUT = int(os.environ.get("CATALOG_CACHE_TIMEOUT", "300"))
