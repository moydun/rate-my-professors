"""Access-control tests for anonymous users on review/report endpoints."""
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .repository import InMemoryRepository
from .seed import seed

PASSWORD = "pass12345"


def _valid_review(overrides=None):
    data = {
        "course": "Алгоритмы",
        "term": "Осень 2025",
        "overall": "4",
        "difficulty": "3",
        "would_take_again": "yes",
        "grade": "A",
        "tags": ["Интересные лекции"],
        "comment": "Понятные лекции и честные экзамены, рекомендую.",
    }
    if overrides:
        data.update(overrides)
    return data


class _PermissionsTestCase(TestCase):
    def setUp(self):
        cache.clear()
        import reviews.views as views
        from reviews import store

        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig_repo = store.repo
        self._orig_view_repo = views.repo
        store.repo = self.repo
        views.repo = self.repo
        self.prof = self.repo.search("Айбек")[0]["prof"]
        self.review = self.repo.reviews_for(self.prof.id)[0]

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._orig_repo
        views.repo = self._orig_view_repo

    def assert_login_redirect(self, response, expected_url):
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertEqual(parse_qs(urlparse(response.url).query)["next"], [expected_url])

    def create_user(self, email="permissions@example.com"):
        return User.objects.create_user(username=email, email=email, password=PASSWORD)


class AnonymousReviewSubmissionTests(_PermissionsTestCase):
    def test_review_form_redirects_to_login(self):
        url = reverse("review_new", args=[self.prof.slug])
        self.assert_login_redirect(self.client.get(url), url)

    def test_review_submission_redirects_to_login_and_is_not_saved(self):
        url = reverse("review_new", args=[self.prof.slug])
        before = len(self.repo.reviews_for(self.prof.id, include_pending=True))
        self.assert_login_redirect(self.client.post(url, _valid_review()), url)
        self.assertEqual(
            len(self.repo.reviews_for(self.prof.id, include_pending=True)), before
        )


class AnonymousReportTests(_PermissionsTestCase):
    def test_report_submission_redirects_to_login_and_is_not_recorded(self):
        url = reverse("review_report", args=[self.review.id])
        before = self.review.reported
        self.assert_login_redirect(self.client.post(url, {"reason": "spam"}), url)
        self.assertEqual(self.review.reported, before)

    def test_authenticated_user_can_report(self):
        self.client.force_login(self.create_user())
        url = reverse("review_report", args=[self.review.id])
        response = self.client.post(url, {"reason": "spam"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.review.reported, 1)


class AnonymousVoteTests(_PermissionsTestCase):
    def test_vote_redirects_to_login_and_is_not_counted(self):
        url = reverse("review_vote", args=[self.review.id])
        before = self.review.helpful
        self.assert_login_redirect(self.client.post(url, {"vote": "up"}), url)
        self.assertEqual(self.review.helpful, before)


class AnonymousProtectedPagesTests(_PermissionsTestCase):
    def test_profile_redirects_to_login(self):
        url = reverse("profile")
        self.assert_login_redirect(self.client.get(url), url)

    def test_add_professor_redirects_to_login(self):
        url = reverse("add_professor")
        self.assert_login_redirect(self.client.get(url), url)
        self.assert_login_redirect(self.client.post(url, {}), url)

    def test_review_edit_redirects_to_login(self):
        url = reverse("review_edit", args=[self.review.id])
        self.assert_login_redirect(self.client.get(url), url)
        self.assert_login_redirect(self.client.post(url, _valid_review()), url)

    def test_review_delete_redirects_to_login_and_keeps_review(self):
        url = reverse("review_delete", args=[self.review.id])
        self.assert_login_redirect(self.client.get(url), url)
        self.assert_login_redirect(self.client.post(url), url)
        self.assertIsNotNone(self.repo.review(self.review.id))