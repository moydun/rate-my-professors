"""Security cookie settings and response-header hardening tests."""
import os
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from config import settings as site


class CookieSettingsTests(TestCase):
    def test_session_cookie_is_httponly_and_samesite(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertIn(settings.SESSION_COOKIE_SAMESITE, {"Lax", "Strict"})

    def test_csrf_cookie_is_samesite(self):
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertIn(settings.CSRF_COOKIE_SAMESITE, {"Lax", "Strict"})

    def test_secure_cookies_track_debug_flag(self):
        debug = site._security_settings(debug=True)
        self.assertFalse(debug["SESSION_COOKIE_SECURE"])
        self.assertFalse(debug["CSRF_COOKIE_SECURE"])

        production = site._security_settings(debug=False)
        self.assertTrue(production["SESSION_COOKIE_SECURE"])
        self.assertTrue(production["CSRF_COOKIE_SECURE"])

    def test_secure_cookies_can_be_overridden(self):
        self.assertTrue(
            site._security_settings(debug=True, session_secure=True)["SESSION_COOKIE_SECURE"]
        )
        self.assertFalse(
            site._security_settings(debug=False, session_secure=False)["SESSION_COOKIE_SECURE"]
        )
        self.assertTrue(
            site._security_settings(debug=True, csrf_secure=True)["CSRF_COOKIE_SECURE"]
        )
        self.assertFalse(
            site._security_settings(debug=False, csrf_secure=False)["CSRF_COOKIE_SECURE"]
        )


class TransportSecuritySettingsTests(TestCase):
    def test_https_hardening_is_off_for_debug(self):
        security = site._security_settings(debug=True)
        self.assertFalse(security["SECURE_SSL_REDIRECT"])
        self.assertEqual(security["SECURE_HSTS_SECONDS"], 0)
        self.assertFalse(security["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertFalse(security["SECURE_HSTS_PRELOAD"])

    def test_https_hardening_is_on_by_default_in_production(self):
        security = site._security_settings(debug=False)
        self.assertTrue(security["SECURE_SSL_REDIRECT"])
        self.assertGreaterEqual(security["SECURE_HSTS_SECONDS"], 31536000)
        self.assertTrue(security["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertTrue(security["SECURE_HSTS_PRELOAD"])

    def test_https_hardening_can_be_overridden_from_env(self):
        self.assertTrue(
            site._security_settings(debug=True, ssl_redirect=True)["SECURE_SSL_REDIRECT"]
        )
        self.assertEqual(
            site._security_settings(debug=False, hsts_seconds=0)["SECURE_HSTS_SECONDS"], 0
        )
        self.assertFalse(
            site._security_settings(
                debug=False, hsts_include_subdomains=False
            )["SECURE_HSTS_INCLUDE_SUBDOMAINS"]
        )
        self.assertFalse(
            site._security_settings(debug=False, hsts_preload=False)["SECURE_HSTS_PRELOAD"]
        )

    def test_header_settings_have_safe_values(self):
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.SECURE_REFERRER_POLICY, "same-origin")
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")

    def test_env_helpers_parse_values(self):
        with patch.dict(os.environ, {"TEST_SECURITY_BOOL": "yes", "TEST_SECURITY_INT": "42"}):
            self.assertTrue(site._get_bool_setting("TEST_SECURITY_BOOL", False))
            self.assertEqual(site._get_int_setting("TEST_SECURITY_INT", 0), 42)
        with patch.dict(os.environ, {"TEST_SECURITY_BOOL": "off", "TEST_SECURITY_INT": "bad"}):
            self.assertFalse(site._get_bool_setting("TEST_SECURITY_BOOL", True))
            self.assertEqual(site._get_int_setting("TEST_SECURITY_INT", 7), 7)


class SecurityHeaderResponseTests(TestCase):
    def setUp(self):
        self.response = self.client.get(reverse("home"))

    def test_home_page_responds(self):
        self.assertEqual(self.response.status_code, 200)

    def test_x_content_type_options_header(self):
        self.assertEqual(self.response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_referrer_policy_header(self):
        self.assertEqual(
            self.response.headers.get("Referrer-Policy"),
            settings.SECURE_REFERRER_POLICY,
        )

    def test_x_frame_options_header(self):
        self.assertEqual(self.response.headers.get("X-Frame-Options"), "DENY")

    def test_permissions_policy_header(self):
        self.assertEqual(
            self.response.headers.get("Permissions-Policy"),
            settings.PERMISSIONS_POLICY,
        )

    def test_content_security_policy_header(self):
        policy = self.response.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    @override_settings(
        X_FRAME_OPTIONS="SAMEORIGIN",
        PERMISSIONS_POLICY="camera=()",
    )
    def test_headers_follow_overridden_settings(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(response.headers.get("Permissions-Policy"), "camera=()")