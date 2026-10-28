"""Review form validation, both directly and through the submission view.

The view-level assertions run against the in-memory and Django ORM
repositories so invalid input can never slip into either backend.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .constants import MAX_COMMENT_LENGTH, MIN_COMMENT_LENGTH
from .forms import ReviewForm
from .tests import _valid


class ReviewFormValidationTests(TestCase):
    """Direct form checks for ratings, comment length and control chars."""

    def test_valid_ratings_are_coerced_to_ints(self):
        for value in (1, 2, 3, 4, 5):
            with self.subTest(rating=value):
                form = ReviewForm(_valid({"overall": str(value), "difficulty": str(value)}))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["overall"], value)
                self.assertEqual(form.cleaned_data["difficulty"], value)

    def test_invalid_ratings_are_rejected(self):
        for field in ("overall", "difficulty"):
            for value in ("", "0", "6", "abc", "4.5", "-1"):
                with self.subTest(field=field, value=value):
                    form = ReviewForm(_valid({field: value}))
                    self.assertFalse(form.is_valid())
                    self.assertIn(field, form.errors)

    def test_empty_and_whitespace_only_comment_are_rejected(self):
        for comment in ("", "   ", "\n\t "):
            with self.subTest(comment=repr(comment)):
                form = ReviewForm(_valid({"comment": comment}))
                self.assertFalse(form.is_valid())
                self.assertIn("comment", form.errors)

    def test_too_short_comment_is_rejected(self):
        form = ReviewForm(_valid({"comment": "я" * (MIN_COMMENT_LENGTH - 1)}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_too_long_comment_is_rejected(self):
        form = ReviewForm(_valid({"comment": "я" * (MAX_COMMENT_LENGTH + 1)}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_boundary_comment_lengths_are_accepted(self):
        for length in (MIN_COMMENT_LENGTH, MAX_COMMENT_LENGTH):
            with self.subTest(length=length):
                form = ReviewForm(_valid({"comment": "я" * length}))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["comment"], "я" * length)

    def test_control_chars_are_stripped_from_text_fields(self):
        form = ReviewForm(_valid({
            "course": "Ал\x07горитмы",
            "term": "Осен\x1bь 2025",
            "comment": "Хороший курс\x07 с примерами\x1b и практикой.",
        }))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["course"], "Алгоритмы")
        self.assertEqual(form.cleaned_data["term"], "Осень 2025")
        self.assertEqual(
            form.cleaned_data["comment"],
            "Хороший курс с примерами и практикой.",
        )

    def test_control_chars_do_not_help_pass_minimum_length(self):
        form = ReviewForm(_valid({
            "comment": "х" * (MIN_COMMENT_LENGTH - 1) + "\x07\x1b",
        }))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_null_byte_is_rejected(self):
        form = ReviewForm(_valid({"comment": "Нормальный отзыв\x00 с нулевым байтом."}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)


class _ReviewFormViewMixin:
    """Submit the review form through the view against either backend."""

    use_django = False

    def setUp(self):
        import reviews.views as views
        from reviews import store as store_mod

        cache.clear()
        self.addCleanup(cache.clear)

        if self.use_django:
            from reviews.django_repo import DjangoRepository
            from reviews.management.commands.seed_kg import Command as SeedCmd

            self.repo = DjangoRepository()
            SeedCmd().handle(clear=True)
        else:
            from reviews.repository import InMemoryRepository
            from reviews.seed import seed

            self.repo = InMemoryRepository()
            seed(self.repo)

        self._orig_store = store_mod.repo
        self._orig_view = views.repo
        store_mod.repo = self.repo
        views.repo = self.repo
        self.addCleanup(self._restore_repos, store_mod, views)

        email = f"review-form-{self.__class__.__name__.lower()}@example.com"
        self.user = User.objects.create_user(
            username=email, email=email, password="pass12345"
        )
        self.client.force_login(self.user)

    def _restore_repos(self, store_mod, views):
        store_mod.repo = self._orig_store
        views.repo = self._orig_view

    def prof(self, q="Айбек"):
        return self.repo.search(q)[0]["prof"]

    def url(self):
        return reverse("review_new", args=[self.prof().slug])

    def pending_count(self):
        return len(self.repo.reviews_for(self.prof().id, include_pending=True))

    def pending_reviews(self):
        return [
            review
            for review in self.repo.reviews_for(self.prof().id, include_pending=True)
            if review.status == "pending"
        ]

    def test_valid_review_is_saved_pending(self):
        before = self.pending_count()
        response = self.client.post(
            self.url(), _valid({"course": "Курс формы", "overall": "5", "difficulty": "1"})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.pending_count(), before + 1)
        pending = self.pending_reviews()
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0].overall, pending[0].difficulty), (5, 1))

    def test_invalid_ratings_are_not_saved(self):
        before = self.pending_count()
        for field in ("overall", "difficulty"):
            for value in ("", "0", "6", "abc", "4.5", "-1"):
                with self.subTest(field=field, value=value):
                    response = self.client.post(self.url(), _valid({field: value}))
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.context["form"].is_valid())
                    self.assertIn(field, response.context["form"].errors)
                    self.assertEqual(self.pending_count(), before)

    def test_empty_comment_is_not_saved(self):
        before = self.pending_count()
        response = self.client.post(self.url(), _valid({"comment": ""}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertEqual(self.pending_count(), before)

    def test_whitespace_only_comment_is_not_saved(self):
        before = self.pending_count()
        response = self.client.post(self.url(), _valid({"comment": "   \n\t "}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertEqual(self.pending_count(), before)

    def test_too_short_comment_is_not_saved(self):
        before = self.pending_count()
        response = self.client.post(
            self.url(), _valid({"comment": "я" * (MIN_COMMENT_LENGTH - 1)})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertEqual(self.pending_count(), before)

    def test_boundary_comment_lengths_are_saved(self):
        before = self.pending_count()
        for length in (MIN_COMMENT_LENGTH, MAX_COMMENT_LENGTH):
            course = f"Курс границы {length}"
            response = self.client.post(
                self.url(),
                _valid({"course": course, "comment": "я" * length}),
            )
            self.assertEqual(response.status_code, 302)
        saved = {
            review.course: review.comment
            for review in self.pending_reviews()
            if review.course.startswith("Курс границы ")
        }
        self.assertEqual(self.pending_count(), before + 2)
        for length in (MIN_COMMENT_LENGTH, MAX_COMMENT_LENGTH):
            self.assertEqual(saved[f"Курс границы {length}"], "я" * length)

    def test_too_long_comment_is_not_saved(self):
        before = self.pending_count()
        response = self.client.post(
            self.url(), _valid({"comment": "я" * (MAX_COMMENT_LENGTH + 1)})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertEqual(self.pending_count(), before)

    def test_control_chars_are_stripped_before_storage(self):
        before = self.pending_count()
        response = self.client.post(
            self.url(), _valid({
                "course": "Курс контроля",
                "comment": "Хороший курс\x07 с примерами\x1b и практикой.",
            })
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.pending_count(), before + 1)
        pending = [r for r in self.pending_reviews() if r.course == "Курс контроля"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].comment, "Хороший курс с примерами и практикой.")
        self.assertEqual(pending[0].course, "Курс контроля")

    def test_null_byte_comment_is_not_saved(self):
        before = self.pending_count()
        response = self.client.post(
            self.url(),
            _valid({"comment": "Нормальный отзыв\x00 с нулевым байтом."}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertEqual(self.pending_count(), before)


class MemoryReviewFormViewTests(_ReviewFormViewMixin, TestCase):
    use_django = False


class DjangoReviewFormViewTests(_ReviewFormViewMixin, TestCase):
    use_django = True