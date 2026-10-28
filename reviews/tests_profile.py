"""Profile page access, ownership, moderation status and i18n tests."""
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .django_repo import DjangoRepository
from .repository import InMemoryRepository

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


class ProfileViewTestMixin:
    def _make_repo(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        import reviews.views as views
        from reviews import store

        self.repo = self._make_repo()
        self._original_store_repo = store.repo
        self._original_view_repo = views.repo
        store.repo = self.repo
        views.repo = self.repo
        school = self.repo.add_school("Тестовый университет", "Бишкек")
        self.prof = self.repo.add_professor(
            school.id, "Иван", "Петров", "Физика", "доцент"
        )
        self.user = self._user("owner@example.com")

    def tearDown(self):
        import reviews.views as views
        from reviews import store

        store.repo = self._original_store_repo
        views.repo = self._original_view_repo
        super().tearDown()

    @staticmethod
    def _user(email):
        return User.objects.create_user(
            username=email, email=email, password=PASSWORD
        )

    def test_profile_requires_login(self):
        url = reverse("profile")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertEqual(parse_qs(urlparse(response.url).query)["next"], [url])

    def test_professor_review_sorting_and_invalid_sort(self):
        from datetime import timedelta

        from django.utils import timezone

        reviews = [
            self.repo.add_review(self.prof.id, {**_review_data("Old low", "Old comment"), "overall": 1},
                                 status="approved"),
            self.repo.add_review(self.prof.id, {**_review_data("Middle", "Middle comment"), "overall": 3},
                                 status="approved"),
            self.repo.add_review(self.prof.id, {**_review_data("New high", "New comment"), "overall": 5},
                                 status="approved"),
        ]
        reviews[0].created_at = timezone.now() - timedelta(days=2)
        reviews[1].created_at = timezone.now() - timedelta(days=1)
        reviews[2].created_at = timezone.now()
        reviews[1].helpful = 3
        reviews[2].helpful = 1
        if isinstance(self.repo, DjangoRepository):
            from .models import Review
            for review in reviews:
                Review.objects.filter(pk=review.id).update(created_at=review.created_at, helpful=review.helpful)
        expected = {
            "new": ["New high", "Middle", "Old low"],
            "old": ["Old low", "Middle", "New high"],
            "high": ["New high", "Middle", "Old low"],
            "low": ["Old low", "Middle", "New high"],
            "helpful": ["Middle", "New high", "Old low"],
            "invalid": ["New high", "Middle", "Old low"],
        }
        for sort, courses in expected.items():
            response = self.client.get(reverse("professor", args=[self.prof.slug]), {"sort": sort})
            self.assertEqual([r.course for r in response.context["reviews"]], courses)
            self.assertEqual(response.context["sort"], sort if sort in expected and sort != "invalid" else "new")

    def test_sort_is_preserved_in_review_pagination(self):
        for index in range(12):
            self.repo.add_review(self.prof.id, _review_data(f"Course {index}", f"Comment {index}"), status="approved")
        response = self.client.get(reverse("professor", args=[self.prof.slug]), {"sort": "low"})
        self.assertContains(response, 'href="?sort=low&page=2"')

    def test_menu_links_to_profile_only_for_authenticated_users(self):
        profile_url = reverse("profile")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, f'href="{profile_url}"')

        self.client.force_login(self.user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'href="{profile_url}"')
        self.assertContains(response, "Мой профиль")

    def test_profile_shows_only_own_reviews_with_every_status(self):
        other_user = self._user("other@example.com")
        for course, status in (
            ("Ожидающий предмет", "pending"),
            ("Опубликованный предмет", "approved"),
            ("Отклонённый предмет", "rejected"),
        ):
            self.repo.add_review(
                self.prof.id,
                _review_data(course, f"Комментарий: {course}"),
                user=self.user,
                status=status,
            )
        self.repo.add_review(
            self.prof.id,
            _review_data("Чужой предмет", "Комментарий другого пользователя"),
            user=other_user,
            status="approved",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reviews/profile.html")
        self.assertEqual(len(response.context["reviews"]), 3)
        self.assertEqual(
            {row["review"].author_id for row in response.context["reviews"]},
            {self.user.id},
        )
        for text in (
            "Ожидающий предмет",
            "Опубликованный предмет",
            "Отклонённый предмет",
            "На проверке",
            "Опубликован",
            "Отклонён",
            "Иван Петров",
            "Тестовый университет",
        ):
            self.assertContains(response, text)
        self.assertNotContains(response, "Чужой предмет")
        self.assertNotContains(response, "Комментарий другого пользователя")

    def test_profile_has_empty_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "owner@example.com")
        self.assertContains(response, "Вы ещё не оставляли отзывов.")
        self.assertNotContains(response, 'class="review profile-review"')

    def test_profile_empty_state_links_to_home(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))
        self.assertContains(response, 'class="empty empty-state"')
        self.assertContains(response, 'class="btn empty-state-action"')
        self.assertContains(response, f'href="{reverse("home")}"')
        self.assertContains(response, "Вернуться на главную")

    def test_profile_renders_status_badge_for_each_moderation_state(self):
        for course, status, label in (
            ("Ожидающий предмет", "pending", "На проверке"),
            ("Опубликованный предмет", "approved", "Опубликован"),
            ("Отклонённый предмет", "rejected", "Отклонён"),
        ):
            self.repo.add_review(
                self.prof.id,
                _review_data(course, f"Комментарий: {course}"),
                user=self.user,
                status=status,
            )

        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))
        for status, label in (
            ("pending", "На проверке"),
            ("approved", "Опубликован"),
            ("rejected", "Отклонён"),
        ):
            with self.subTest(status=status):
                self.assertContains(response, f'class="status-badge {status}"')
                self.assertContains(response, label)

    def test_owner_can_edit_and_review_returns_to_pending(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Исходный комментарий достаточно длинный."),
            user=self.user,
            status="approved",
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("review_edit", args=[review.id]), {
            **_review_data("Алгоритмы", "Изменённый комментарий достаточно длинный."),
            "overall": "3", "would_take_again": "no",
        })
        self.assertRedirects(response, reverse("profile"))
        changed = self.repo.review(review.id)
        self.assertEqual(changed.comment, "Изменённый комментарий достаточно длинный.")
        self.assertEqual(changed.status, "pending")
        self.assertNotIn(
            changed.id,
            [item.id for item in self.repo.reviews_for(self.prof.id)],
        )

    def test_owner_can_delete_only_with_post(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )
        self.client.force_login(self.user)
        url = reverse("review_delete", args=[review.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIsNotNone(self.repo.review(review.id))
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertIsNone(self.repo.review(review.id))

    def test_other_user_cannot_edit_or_delete(self):
        original_comment = "Комментарий достаточно длинный для теста."
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", original_comment),
            user=self.user,
            status="approved",
        )
        other = self._user("other-owner@example.com")
        self.client.force_login(other)
        edit_url = reverse("review_edit", args=[review.id])
        delete_url = reverse("review_delete", args=[review.id])

        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertEqual(
            self.client.post(
                edit_url,
                _review_data("Взломанный предмет", "Попытка изменить чужой отзыв."),
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(delete_url).status_code, 404)
        self.assertEqual(self.client.post(delete_url).status_code, 404)

        unchanged = self.repo.review(review.id)
        self.assertIsNotNone(unchanged)
        self.assertEqual(unchanged.comment, original_comment)
        self.assertEqual(unchanged.status, "approved")

    def test_edit_and_delete_forms_include_csrf_tokens(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )
        self.client.force_login(self.user)
        for url_name in ("review_edit", "review_delete"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name, args=[review.id]))
                self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_edit_and_delete_require_csrf_tokens(self):
        original_comment = "Комментарий достаточно длинный для теста."
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", original_comment),
            user=self.user,
            status="approved",
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(
            reverse("review_edit", args=[review.id]),
            _review_data("Алгоритмы", "Изменение без CSRF-токена не должно сохраниться."),
        )
        self.assertEqual(response.status_code, 403)
        unchanged = self.repo.review(review.id)
        self.assertEqual(unchanged.comment, original_comment)
        self.assertEqual(unchanged.status, "approved")

        response = client.post(reverse("review_delete", args=[review.id]))
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.repo.review(review.id))

    def test_anonymous_edit_redirects_to_login(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )
        response = self.client.get(reverse("review_edit", args=[review.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_anonymous_user_is_redirected_from_protected_get_views(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )
        protected_urls = (
            reverse("profile"),
            reverse("review_new", args=[self.prof.slug]),
            reverse("review_edit", args=[review.id]),
            reverse("review_delete", args=[review.id]),
            reverse("add_professor"),
        )

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlparse(response.url).path, reverse("login"))
                self.assertEqual(
                    parse_qs(urlparse(response.url).query)["next"], [url]
                )

    def test_anonymous_user_is_redirected_from_protected_post_views(self):
        review = self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Комментарий достаточно длинный для теста."),
            user=self.user,
        )

        for url_name in ("review_vote", "review_report"):
            url = reverse(url_name, args=[review.id])
            with self.subTest(url_name=url_name):
                response = self.client.post(url, {})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlparse(response.url).path, reverse("login"))
                self.assertEqual(
                    parse_qs(urlparse(response.url).query)["next"], [url]
                )

    def test_profile_heading_and_status_are_translated(self):
        self.repo.add_review(
            self.prof.id,
            _review_data("Алгоритмы", "Отзыв для проверки перевода"),
            user=self.user,
            status="pending",
        )
        self.client.force_login(self.user)
        for language, heading, status in (
            ("ru", "Мой профиль", "На проверке"),
            ("ky", "Менин профилим", "Текшерилүүдө"),
            ("en", "My profile", "Under review"),
        ):
            with self.subTest(language=language):
                self.client.post(
                    reverse("set_language"),
                    {"language": language, "next": reverse("profile")},
                )
                response = self.client.get(reverse("profile"))
                self.assertContains(response, heading)
                self.assertContains(response, status)


class MemoryProfileViewTests(ProfileViewTestMixin, TestCase):
    def _make_repo(self):
        return InMemoryRepository()


class DjangoProfileViewTests(ProfileViewTestMixin, TestCase):
    def _make_repo(self):
        return DjangoRepository()
