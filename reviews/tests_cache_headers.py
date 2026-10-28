"""Cache-Control header tests: user-data pages are non-cacheable, public pages untouched."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .repository import InMemoryRepository
from .seed import seed

PASSWORD = "pass12345"


def _review_data(course, comment):
    return {
        "course": course,
        "term": "Осень 2026",
        "overall": 4,
        "difficulty": 3,
        "would_take_again": True,
        "grade": "A",
        "comment": comment,
        "tags": ["Интересные лекции"],
    }


class CacheControlTestCase(TestCase):
    def setUp(self):
        import reviews.views as views
        from reviews import store

        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig_repo = store.repo
        self._orig_view_repo = views.repo
        store.repo = self.repo
        views.repo = self.repo
        self.prof = self.repo.search("Айбек")[0]["prof"]
        self.user = User.objects.create_user(
            username="user@example.com", email="user@example.com", password=PASSWORD
        )

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._orig_repo
        views.repo = self._orig_view_repo

    def login(self):
        self.client.login(username="user@example.com", password=PASSWORD)

    def assert_private(self, response):
        self.assertNotEqual(response.status_code, 404)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)

    def assert_public(self, response):
        self.assertEqual(response.status_code, 200)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertNotIn("no-store", cache_control)
        self.assertNotIn("private", cache_control)


class PrivatePagesCacheControlTests(CacheControlTestCase):
    def test_login_page_is_not_cacheable(self):
        self.assert_private(self.client.get(reverse("login")))

    def test_signup_page_is_not_cacheable(self):
        self.assert_private(self.client.get(reverse("signup")))

    def test_profile_page_is_not_cacheable(self):
        self.login()
        self.assert_private(self.client.get(reverse("profile")))

    def test_review_form_is_not_cacheable(self):
        self.login()
        self.assert_private(self.client.get(reverse("review_new", args=[self.prof.slug])))

    def test_review_edit_page_is_not_cacheable(self):
        self.login()
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )
        self.assert_private(self.client.get(reverse("review_edit", args=[review.id])))

    def test_review_report_response_is_not_cacheable(self):
        self.login()
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            status="approved",
        )
        response = self.client.post(
            reverse("review_report", args=[review.id]), {"reason": "spam"}
        )
        self.assert_private(response)

    def test_security_headers_are_preserved_on_private_pages(self):
        self.login()
        response = self.client.get(reverse("profile"))
        self.assertEqual(
            response.headers.get("Permissions-Policy"),
            "camera=(), geolocation=(), microphone=()",
        )
        self.assertIn("default-src", response.headers.get("Content-Security-Policy", ""))
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")


class PublicPagesCacheControlTests(CacheControlTestCase):
    def test_home_page_has_no_no_store(self):
        response = self.client.get(reverse("home"))
        self.assert_public(response)
        self.assertIn("public", response.headers.get("Cache-Control", ""))
        self.assertIn("max-age=300", response.headers.get("Cache-Control", ""))

    def test_about_page_has_no_no_store(self):
        self.assert_public(self.client.get(reverse("about")))

    def test_school_page_has_no_no_store(self):
        school = self.repo.school(self.prof.school_id)
        self.assert_public(self.client.get(reverse("school_detail", args=[school.slug])))

    def test_professor_page_has_no_no_store(self):
        self.assert_public(self.client.get(reverse("professor", args=[self.prof.slug])))

    def test_sitemap_responses_are_publicly_cacheable(self):
        for url in (reverse("sitemap_index"), "/sitemap-home.xml"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assert_public(response)
                self.assertIn("public", response.headers.get("Cache-Control", ""))
                self.assertIn("max-age=300", response.headers.get("Cache-Control", ""))

    def test_authenticated_home_and_sitemaps_are_not_shared_cacheable(self):
        self.login()
        for url in (reverse("home"), reverse("sitemap_index"), "/sitemap-home.xml"):
            with self.subTest(url=url):
                response = self.client.get(url)
                cache_control = response.headers.get("Cache-Control", "")
                self.assertIn("private", cache_control)
                self.assertIn("no-store", cache_control)

    def test_non_get_home_request_is_not_publicly_cacheable(self):
        response = self.client.post(reverse("home"))
        self.assertNotIn("public", response.headers.get("Cache-Control", ""))
