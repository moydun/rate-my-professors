"""CSRF protection for rendered forms and state-changing endpoints."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from .repository import InMemoryRepository
from .seed import seed


class CsrfFormTests(TestCase):
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
        self.client = Client(enforce_csrf_checks=True)

    def _owned_review(self, user):
        self.repo.add_review(
            self.prof.id,
            {
                "course": "Курс для проверки",
                "term": "Осень 2025",
                "overall": 4,
                "difficulty": 3,
                "would_take_again": True,
                "grade": "A",
                "tags": [],
                "comment": "Достаточно содержательный комментарий для проверки формы.",
            },
            user=user,
        )
        return self.repo.review(self.repo.reviews_for_user(user)[0]["review"].id)

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._orig_repo
        views.repo = self._orig_view_repo

    def test_rendered_post_forms_include_csrf_inputs(self):
        user = User.objects.create_user(username="csrf@example.com", password="pass12345")
        self.client.force_login(user)
        owned_review = self._owned_review(user)
        urls = [
            reverse("review_new", args=[self.prof.slug]),
            reverse("add_professor"),
            reverse("review_edit", args=[owned_review.id]),
            reverse("review_delete", args=[owned_review.id]),
            reverse("professor", args=[self.prof.slug]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="csrfmiddlewaretoken"')

        self.client.logout()
        for url in (reverse("login"), reverse("signup")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_state_changing_post_endpoints_reject_missing_csrf_token(self):
        user = User.objects.create_user(username="csrf@example.com", password="pass12345")
        self.client.force_login(user)
        owned_review = self._owned_review(user)
        urls = [
            reverse("review_new", args=[self.prof.slug]),
            reverse("add_professor"),
            reverse("review_edit", args=[owned_review.id]),
            reverse("review_delete", args=[owned_review.id]),
            reverse("review_vote", args=[self.review.id]),
            reverse("review_report", args=[self.review.id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 403)
