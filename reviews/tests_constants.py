"""Tests that shared limits are defined once and wired into the app."""
from django.conf import settings
from django.test import SimpleTestCase

from .constants import MAX_COMMENT_LENGTH, MIN_COMMENT_LENGTH, PAGE_SIZE, RATE_LIMITS
from .views import _paginate


class SharedConstantsTests(SimpleTestCase):
    def test_review_comment_limits_are_sane(self):
        self.assertEqual(MIN_COMMENT_LENGTH, 20)
        self.assertEqual(MAX_COMMENT_LENGTH, 1000)
        self.assertLess(MIN_COMMENT_LENGTH, MAX_COMMENT_LENGTH)

    def test_sanitize_reexports_comment_limits(self):
        from . import sanitize

        self.assertEqual(sanitize.MIN_COMMENT_LENGTH, MIN_COMMENT_LENGTH)
        self.assertEqual(sanitize.MAX_COMMENT_LENGTH, MAX_COMMENT_LENGTH)

    def test_pagination_uses_page_size_constant(self):
        items = list(range(PAGE_SIZE + 1))
        page_items, page_obj = _paginate(items, 1)

        self.assertEqual(len(page_items), PAGE_SIZE)
        self.assertEqual(page_obj.paginator.per_page, PAGE_SIZE)

    def test_settings_rate_limits_match_constants(self):
        self.assertEqual(settings.RATELIMIT, RATE_LIMITS)

    def test_expected_rate_limit_actions_are_defined(self):
        self.assertEqual(
            set(RATE_LIMITS), {"login", "signup", "review", "report", "vote"}
        )
        for limit, window in RATE_LIMITS.values():
            self.assertGreater(limit, 0)
            self.assertGreater(window, 0)

    def test_review_and_report_allow_five_posts_per_minute(self):
        self.assertEqual(RATE_LIMITS["review"], (5, 60))
        self.assertEqual(RATE_LIMITS["report"], (5, 60))
