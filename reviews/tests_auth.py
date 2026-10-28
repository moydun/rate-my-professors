"""Authentication flows and access-control checks for login/signup/logout."""
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


class _AuthTestCase(TestCase):
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

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._orig_repo
        views.repo = self._orig_view_repo

    def create_user(self, email="user@example.com"):
        return User.objects.create_user(username=email, email=email, password=PASSWORD)

    def login_via_view(self, email="user@example.com", next_url=""):
        data = {"username": email, "password": PASSWORD}
        if next_url:
            data["next"] = next_url
        return self.client.post(reverse("login"), data)


class LoginFlowTests(_AuthTestCase):
    def test_login_page_renders(self):
        r = self.client.get(reverse("login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="username"')
        self.assertContains(r, 'name="password"')

    def test_valid_credentials_log_user_in(self):
        self.create_user()
        r = self.login_via_view()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_credentials_do_not_log_in(self):
        self.create_user()
        r = self.client.post(
            reverse("login"), {"username": "user@example.com", "password": "wrong-pass"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_redirects_to_next(self):
        self.create_user()
        target = reverse("rules")
        r = self.login_via_view(next_url=target)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, target)


class SignupFlowTests(_AuthTestCase):
    def test_signup_creates_user_and_logs_in(self):
        r = self.client.post(
            reverse("signup"),
            {
                "email": "new@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))
        self.assertTrue(User.objects.filter(username="new@example.com").exists())
        self.assertIn("_auth_user_id", self.client.session)

    def test_signup_duplicate_email_rejected(self):
        self.create_user(email="dup@example.com")
        r = self.client.post(
            reverse("signup"),
            {
                "email": "dup@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("email", r.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_signup_password_mismatch_rejected(self):
        r = self.client.post(
            reverse("signup"),
            {
                "email": "mismatch@example.com",
                "password1": PASSWORD,
                "password2": "different-pass",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="mismatch@example.com").exists())

    def test_authenticated_user_is_redirected_home(self):
        user = self.create_user()
        self.client.force_login(user)
        r = self.client.get(reverse("signup"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))


class LogoutFlowTests(_AuthTestCase):
    def test_logout_clears_session_and_redirects_home(self):
        user = self.create_user()
        self.client.force_login(user)
        r = self.client.post(reverse("logout"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        user = self.create_user()
        self.client.force_login(user)
        r = self.client.get(reverse("logout"))
        self.assertEqual(r.status_code, 405)

    def test_logout_rejects_external_next_urls(self):
        user = self.create_user()
        for target in ("https://evil.com/", "//evil.com/", "javascript:alert(1)"):
            with self.subTest(target=target):
                self.client.force_login(user)
                r = self.client.post(reverse("logout"), {"next": target})
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.url, reverse("home"))

    def test_after_logout_protected_page_redirects_to_login(self):
        user = self.create_user()
        self.client.force_login(user)
        self.client.post(reverse("logout"))
        r = self.client.get(reverse("review_new", args=[self.prof.slug]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)


class OpenRedirectTests(_AuthTestCase):
    UNSAFE_TARGETS = ("//evil.com", "https://evil.com", "javascript:alert(1)")

    def setUp(self):
        super().setUp()
        self.user = self.create_user()

    def test_login_rejects_unsafe_next_and_falls_back_home(self):
        for target in self.UNSAFE_TARGETS:
            with self.subTest(target=target):
                r = self.login_via_view(next_url=target)
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.url, reverse("home"))
                self.assertNotIn("evil.com", r.url)

    def test_login_allows_relative_next(self):
        r = self.login_via_view(next_url="/path")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/path")

    def test_logout_rejects_unsafe_next_and_falls_back_home(self):
        for target in self.UNSAFE_TARGETS:
            with self.subTest(target=target):
                self.client.force_login(self.user)
                r = self.client.post(reverse("logout"), {"next": target})
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.url, reverse("home"))
                self.assertNotIn("evil.com", r.url)

    def test_logout_allows_relative_next(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("logout"), {"next": "/path"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/path")


class AnonymousPermissionTests(_AuthTestCase):
    def test_anonymous_cannot_open_review_form(self):
        url = reverse("review_new", args=[self.prof.slug])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)
        self.assertEqual(parse_qs(urlparse(r.url).query)["next"], [url])

    def test_anonymous_cannot_submit_review(self):
        url = reverse("review_new", args=[self.prof.slug])
        before = len(self.repo.reviews_for(self.prof.id, include_pending=True))
        r = self.client.post(url, _valid_review())
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)
        self.assertEqual(parse_qs(urlparse(r.url).query)["next"], [url])
        self.assertEqual(
            len(self.repo.reviews_for(self.prof.id, include_pending=True)), before
        )

    def test_login_with_next_returns_to_review_form(self):
        self.create_user()
        url = reverse("review_new", args=[self.prof.slug])
        r = self.login_via_view(next_url=url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, url)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_login_rejects_external_next_urls(self):
        self.create_user()
        for target in ("https://evil.com/", "//evil.com/", "javascript:alert(1)"):
            with self.subTest(target=target):
                r = self.login_via_view(next_url=target)
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.url, reverse("home"))

    def test_authenticated_user_can_submit_review(self):
        user = self.create_user()
        self.client.force_login(user)
        url = reverse("review_new", args=[self.prof.slug])
        r = self.client.post(url, _valid_review())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("professor", args=[self.prof.slug]))

    def test_anonymous_cannot_vote(self):
        review = self.repo.reviews_for(self.prof.id)[0]
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)

    def test_anonymous_cannot_add_professor(self):
        r = self.client.get(reverse("add_professor"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)
