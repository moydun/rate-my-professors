"""Boundary tests for text sanitization and request throttling."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from .constants import MAX_COMMENT_LENGTH
from .sanitize import clean_text, strip_control_chars
from .throttle import is_rate_limited


class SanitizeTests(SimpleTestCase):
    def test_strip_control_chars_removes_c0_and_c1_except_whitespace_controls(self):
        value = "a" + "".join(chr(code) for code in range(32)) + "\x7f\x85z"
        self.assertEqual(strip_control_chars(value), "a\t\n\rz")

    def test_empty_and_none_values(self):
        for value in ("", None):
            with self.subTest(value=value):
                self.assertEqual(strip_control_chars(value), "")
                self.assertEqual(clean_text(value), "")

    def test_clean_text_normalizes_newlines_and_strips_outer_whitespace(self):
        self.assertEqual(clean_text(" \t hello\r\nworld\r \n "), "hello\nworld")

    def test_maximum_length_text_is_not_truncated(self):
        value = "x" * MAX_COMMENT_LENGTH
        self.assertEqual(clean_text(value), value)
        self.assertEqual(len(clean_text(value)), MAX_COMMENT_LENGTH)


class ThrottleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.request = RequestFactory().post("/")
        self.request.META["REMOTE_ADDR"] = "192.0.2.1"

    @override_settings(RATELIMIT_ENABLE=True, RATELIMIT={"test": (2, 10)})
    def test_allows_limit_then_rejects_excess_hits(self):
        self.assertFalse(is_rate_limited(self.request, "test"))
        self.assertFalse(is_rate_limited(self.request, "test"))
        self.assertTrue(is_rate_limited(self.request, "test"))

    @override_settings(RATELIMIT_ENABLE=True, RATELIMIT={"test": (1, 10)})
    def test_fixed_window_resets_when_cache_entry_expires(self):
        now = [100.0]
        with patch("django.core.cache.backends.locmem.time.time", side_effect=lambda: now[0]):
            self.assertFalse(is_rate_limited(self.request, "test"))
            self.assertTrue(is_rate_limited(self.request, "test"))
            now[0] = 111.0
            self.assertFalse(is_rate_limited(self.request, "test"))

    @override_settings(RATELIMIT_ENABLE=True, RATELIMIT={"test": (1, 10)})
    def test_counters_are_independent_per_subject(self):
        other_request = RequestFactory().post("/")
        other_request.META["REMOTE_ADDR"] = "192.0.2.2"
        self.assertFalse(is_rate_limited(self.request, "test"))
        self.assertTrue(is_rate_limited(self.request, "test"))
        self.assertFalse(is_rate_limited(other_request, "test"))

    @override_settings(RATELIMIT_ENABLE=True, RATELIMIT={"test": (1, 10)})
    def test_authenticated_user_is_independent_of_other_users(self):
        user_a, user_b = _User("alice"), _User("bob")
        self.assertFalse(is_rate_limited(self.request, "test", user_a))
        self.assertTrue(is_rate_limited(self.request, "test", user_a))
        self.assertFalse(is_rate_limited(self.request, "test", user_b))


class _User:
    is_authenticated = True

    def __init__(self, username):
        self.username = username

    def get_username(self):
        return self.username
