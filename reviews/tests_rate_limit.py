"""Per-IP rate limiting for review submissions and reports."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import store as store_mod
from . import views as views_mod
from .constants import RATE_LIMITS
from .repository import InMemoryRepository
from .seed import seed


def _valid(course="Алгоритмы"):
    return {
        "course": course,
        "term": "Осень 2025",
        "overall": "4",
        "difficulty": "3",
        "would_take_again": "yes",
        "grade": "A",
        "tags": ["Интересные лекции"],
        "comment": "Понятные лекции и честные экзамены, рекомендую.",
    }


class _MemoryRepoTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig = store_mod.repo
        self._orig_v = views_mod.repo
        store_mod.repo = self.repo
        views_mod.repo = self.repo
        self.addCleanup(self._restore)
        self.addCleanup(cache.clear)

    def _restore(self):
        store_mod.repo = self._orig
        views_mod.repo = self._orig_v

    def login(self):
        user = User.objects.create_user(
            username="rate@example.com", email="rate@example.com", password="pass12345"
        )
        self.client.force_login(user)
        return user

    def professor(self):
        return self.repo.search("Айбек")[0]["prof"]


@override_settings(
    RATELIMIT_ENABLE=True, RATELIMIT={"review": (2, 60), "report": (2, 60)}
)
class ReviewSubmissionRateLimitTests(_MemoryRepoTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.url = reverse("review_new", args=[self.professor().slug])

    def _post(self, course, ip="127.0.0.1"):
        return self.client.post(self.url, _valid(course), REMOTE_ADDR=ip)

    def test_review_budget_is_spent_then_returns_429(self):
        self.assertEqual(self._post("Курс 1").status_code, 302)
        self.assertEqual(self._post("Курс 2").status_code, 302)
        self.assertEqual(self._post("Курс 3").status_code, 429)

    def test_review_limits_are_isolated_per_ip(self):
        self._post("Курс 1", ip="10.0.0.1")
        self._post("Курс 2", ip="10.0.0.1")
        self.assertEqual(self._post("Курс 3", ip="10.0.0.1").status_code, 429)
        self.assertEqual(self._post("Курс 3", ip="10.0.0.2").status_code, 302)

    def test_review_page_get_is_not_limited(self):
        self._post("Курс 1")
        self._post("Курс 2")
        self.assertEqual(self._post("Курс 3").status_code, 429)
        self.assertEqual(self.client.get(self.url).status_code, 200)


@override_settings(
    RATELIMIT_ENABLE=True, RATELIMIT={"review": (2, 60), "report": (2, 60)}
)
class ReportRateLimitTests(_MemoryRepoTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        review = self.repo.reviews_for(self.professor().id)[0]
        self.url = reverse("review_report", args=[review.id])

    def _post(self, ip="127.0.0.1"):
        return self.client.post(self.url, {"reason": "spam"}, REMOTE_ADDR=ip)

    def test_report_budget_is_spent_then_returns_429(self):
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(self._post().status_code, 429)

    def test_report_limits_are_isolated_per_ip(self):
        self._post(ip="10.0.0.1")
        self._post(ip="10.0.0.1")
        self.assertEqual(self._post(ip="10.0.0.1").status_code, 429)
        self.assertEqual(self._post(ip="10.0.0.2").status_code, 302)

    def test_report_and_review_budgets_are_independent(self):
        self._post()
        self._post()
        self.assertEqual(self._post().status_code, 429)
        review_url = reverse("review_new", args=[self.professor().slug])
        self.assertEqual(
            self.client.post(review_url, _valid()).status_code, 302
        )


class RateLimitDefaultsTests(TestCase):
    def test_review_and_report_allow_five_posts_per_minute(self):
        self.assertEqual(RATE_LIMITS["review"], (5, 60))
        self.assertEqual(RATE_LIMITS["report"], (5, 60))
