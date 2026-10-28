"""Query-count tests for the profile and professor pages."""
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .django_repo import DjangoRepository


def _review_data(course, tags=None):
    return {
        "course": course,
        "term": "Осень 2026",
        "overall": 4,
        "difficulty": 3,
        "would_take_again": True,
        "grade": "A",
        "comment": "Достаточно длинный комментарий для теста запросов.",
        "tags": tags or ["Интересные лекции"],
    }


@override_settings(CACHES={
    "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
})
class PageQueryCountTests(TestCase):
    def setUp(self):
        import reviews.views as views
        from reviews import store

        self.repo = DjangoRepository()
        self._original_store_repo = store.repo
        self._original_view_repo = views.repo
        store.repo = self.repo
        views.repo = self.repo

        self.user = User.objects.create_user(
            username="querycount@example.com",
            email="querycount@example.com",
            password="pass12345",
        )
        self.school = self.repo.add_school("Тестовый университет", "Бишкек")
        self.prof = self.repo.add_professor(
            self.school.id, "Иван", "Петров", "Физика", "доцент"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._original_store_repo
        views.repo = self._original_view_repo
        super().tearDown()

    def _add_reviews(self, prefix, count):
        for index in range(count):
            self.repo.add_review(
                self.prof.id,
                _review_data(
                    f"{prefix} {index}",
                    tags=["Интересные лекции", "Много тестов"],
                ),
                user=self.user,
                status="approved",
            )

    def _query_count(self, url, data=None):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url, data or {})
        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_profile_query_count_does_not_grow_with_reviews(self):
        url = reverse("profile")
        self._add_reviews("Базовый", 2)
        self.client.get(url)  # warm session storage

        before = self._query_count(url)
        self._add_reviews("Больше", 6)
        with self.assertNumQueries(before):
            response = self.client.get(url)
        self.assertEqual(len(response.context["reviews"]), 8)

    def test_professor_sort_query_count_does_not_grow_with_reviews(self):
        url = reverse("professor", args=[self.prof.slug])
        sorts = ("new", "old", "high", "low", "helpful")
        self._add_reviews("Базовый", 2)
        self.client.get(url)  # warm session storage

        before = {
            sort: self._query_count(url, {"sort": sort})
            for sort in sorts
        }

        self._add_reviews("Больше", 6)

        for sort in sorts:
            with self.subTest(sort=sort):
                with self.assertNumQueries(before[sort]):
                    response = self.client.get(url, {"sort": sort})
                self.assertEqual(len(response.context["reviews"]), 8)