"""HTTP method restrictions and CSRF enforcement for state-changing views."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from .repository import InMemoryRepository
from .seed import seed


class ViewMethodSecurityTests(TestCase):
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
        self.user = User.objects.create_user(
            username="methods@example.com",
            email="methods@example.com",
            password="pass12345",
        )

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._orig_repo
        views.repo = self._orig_view_repo

    def _owned_review(self):
        self.repo.add_review(
            self.prof.id,
            {
                "course": "Курс для проверки методов",
                "term": "Осень 2025",
                "overall": 4,
                "difficulty": 3,
                "would_take_again": True,
                "grade": "A",
                "tags": [],
                "comment": "Достаточно содержательный комментарий для проверки методов.",
            },
            user=self.user,
        )
        return self.repo.review(self.repo.reviews_for_user(self.user)[0]["review"].id)

    def test_post_only_views_reject_get(self):
        urls = [
            reverse("review_vote", args=[self.review.id]),
            reverse("review_report", args=[self.review.id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = Client().get(url)
                self.assertEqual(response.status_code, 405)

    def test_form_views_reject_unsupported_methods(self):
        owned_review = self._owned_review()
        self.client.force_login(self.user)
        urls = [
            reverse("review_new", args=[self.prof.slug]),
            reverse("review_edit", args=[owned_review.id]),
            reverse("review_delete", args=[owned_review.id]),
            reverse("add_professor"),
            reverse("signup"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.put(url)
                self.assertEqual(response.status_code, 405)

    def test_state_changing_views_reject_missing_csrf_token(self):
        owned_review = self._owned_review()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        urls = [
            reverse("review_new", args=[self.prof.slug]),
            reverse("review_edit", args=[owned_review.id]),
            reverse("review_delete", args=[owned_review.id]),
            reverse("review_vote", args=[self.review.id]),
            reverse("review_report", args=[self.review.id]),
            reverse("add_professor"),
            reverse("signup"),
            reverse("login"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(client.post(url, {}).status_code, 403)
