"""Application-specific HTTP middleware."""
from django.conf import settings
from django.utils.cache import patch_cache_control


class PermissionsPolicyMiddleware:
    """Add the configured Permissions-Policy header to every response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers["Permissions-Policy"] = settings.PERMISSIONS_POLICY
        return response


# URL names of pages that render or collect user-specific data. Responses for
# these views must never be stored by shared or browser caches.
PRIVATE_CACHE_URL_NAMES = frozenset({
    "profile",
    "review_new",
    "review_edit",
    "review_report",
    "login",
    "signup",
})

PUBLIC_CACHE_URL_NAMES = frozenset({
    "home",
    "sitemap_index",
    "django.contrib.sitemaps.views.sitemap",
})


class PrivateCacheControlMiddleware:
    """Send ``Cache-Control: private, no-store`` on user-specific pages.

    Public pages (home, school/professor detail, about, static) are left
    untouched. Only the Cache-Control header is patched; other security
    headers (CSP, Permissions-Policy, ...) are preserved. The resolver match
    is read after the view ran, so it is available even for redirects and
    error responses produced by matched views.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = request.resolver_match
        if match is not None and match.url_name in PRIVATE_CACHE_URL_NAMES:
            patch_cache_control(response, private=True, no_store=True)
        elif (
            match is not None
            and match.url_name in PUBLIC_CACHE_URL_NAMES
            and request.method == "GET"
        ):
            if request.user.is_authenticated:
                patch_cache_control(response, private=True, no_store=True)
            elif response.status_code == 200:
                patch_cache_control(response, public=True, max_age=300)
        return response
