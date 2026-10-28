"""Edge-case tests: professor search/filter/sort and form validation.

Kept separate from tests.py to group boundary/validation coverage. The
repository contract block runs the same assertions against both backends so
the in-memory and ORM implementations cannot drift apart.
"""
from django.test import TestCase
from django.urls import reverse

from .django_repo import DjangoRepository
from .forms import ReviewForm
from .repository import InMemoryRepository
from .tests import _MemoryRepoTestCase, _repo_review, _valid


# ---------------------------------------------------------------------------
# Search / filter / sort contract: identical assertions for BOTH backends
# ---------------------------------------------------------------------------
class SearchEdgeContractMixin:
    def _make(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repo = self._make()
        self.s = self.repo.add_school("Тестовый университет", "Бишкек")
        self.p1 = self.repo.add_professor(self.s.id, "Иван", "Петров", "Физика", "доцент")
        self.p2 = self.repo.add_professor(self.s.id, "Пётр", "Сидоров", "Математика", "доцент")
        self.repo.add_review(self.p1.id, _repo_review(course="К1", overall=3), status="approved")
        self.repo.add_review(self.p1.id, _repo_review(course="К2", overall=3), status="approved")
        self.repo.add_review(self.p2.id, _repo_review(course="К3", overall=5), status="approved")

    def _ids(self, **kwargs):
        return [x["prof"].id for x in self.repo.search(**kwargs)]

    def test_whitespace_only_query_returns_everything(self):
        self.assertEqual(sorted(self._ids(q="   ")), sorted([self.p1.id, self.p2.id]))

    def test_empty_filters_return_everything(self):
        self.assertEqual(sorted(self._ids(q="", city="", school_slug="", department="")),
                         sorted([self.p1.id, self.p2.id]))

    def test_query_is_case_insensitive_and_matches_department(self):
        self.assertEqual(self._ids(q="петров"), [self.p1.id])
        self.assertEqual(self._ids(q="ПЕТРОВ"), [self.p1.id])
        # "Физика" is the department of p1, not part of the name
        self.assertEqual(self._ids(q="физика"), [self.p1.id])

    def test_department_filter_ignores_surrounding_whitespace(self):
        self.assertEqual(self._ids(department="  Физика  "), [self.p1.id])
        self.assertEqual(self._ids(department="Физика"), [self.p1.id])

    def test_unknown_filter_values_return_empty(self):
        self.assertEqual(self._ids(q="такого-нет"), [])
        self.assertEqual(self._ids(city="Атлантида"), [])
        self.assertEqual(self._ids(school_slug="no-such-school"), [])
        self.assertEqual(self._ids(department="Несуществующая"), [])

    def test_conflicting_filters_return_empty(self):
        # school belongs to Бишкек, so asking for another city must yield nothing
        self.assertEqual(self._ids(school_slug=self.s.slug, city="Ош"), [])

    def test_sort_invalid_falls_back_to_rating(self):
        top = self.repo.search("", sort="bogus")[0]
        self.assertEqual(top["prof"].id, self.p2.id)  # overall 5 > 3

    def test_sort_rating_orders_by_overall_desc(self):
        rated = self.repo.search("", sort="rating")
        self.assertEqual(rated[0]["prof"].id, self.p2.id)
        self.assertGreaterEqual(rated[0]["stats"]["overall"], rated[1]["stats"]["overall"])

    def test_sort_reviews_orders_by_count_desc(self):
        by_reviews = self.repo.search("", sort="reviews")
        self.assertEqual(by_reviews[0]["prof"].id, self.p1.id)  # 2 reviews > 1
        self.assertGreaterEqual(by_reviews[0]["stats"]["count"], by_reviews[1]["stats"]["count"])

    def test_pending_professor_never_leaks_through_filters(self):
        pending = self.repo.add_professor(self.s.id, "Скрытый", "Профессор", "Физика", "доцент",
                                          status="pending")
        self.assertNotIn(pending.id, self._ids())
        self.assertNotIn(pending.id, self._ids(department="Физика"))
        self.assertNotIn(pending.id, self._ids(city="Бишкек"))
        self.assertNotIn(pending.id, self._ids(q="Скрытый"))


class MemorySearchEdgeTests(SearchEdgeContractMixin, TestCase):
    def _make(self):
        return InMemoryRepository()


class DjangoSearchEdgeTests(SearchEdgeContractMixin, TestCase):
    def _make(self):
        return DjangoRepository()


# ---------------------------------------------------------------------------
# View-level search/filter/sort edge cases (seeded in-memory data)
# ---------------------------------------------------------------------------
class HomeSearchEdgeViewTests(_MemoryRepoTestCase):
    def test_whitespace_only_query_lists_everyone(self):
        r = self.client.get(reverse("home"), {"q": "   "})
        self.assertContains(r, "Айбек")
        self.assertContains(r, "Волков")

    def test_unknown_city_shows_empty_state(self):
        r = self.client.get(reverse("home"), {"city": "Атлантида"})
        self.assertContains(r, "Ничего не нашлось")

    def test_empty_filters_list_all(self):
        r = self.client.get(reverse("home"), {"q": "", "city": "", "school": "", "department": ""})
        self.assertContains(r, "Айбек")

    def test_page_out_of_range_returns_last_page(self):
        r = self.client.get(reverse("home"), {"page": "999"})
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.context["results"]), 10)

    def test_non_numeric_page_falls_back_to_first(self):
        r = self.client.get(reverse("home"), {"page": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["results"])

    def test_negative_page_falls_back_to_first(self):
        r = self.client.get(reverse("home"), {"page": "-3"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["results"])

    def test_combined_city_and_department_filter(self):
        r = self.client.get(reverse("home"), {"city": "Бишкек", "department": "Информатика"})
        self.assertContains(r, "Айбек")
        self.assertNotContains(r, "Бакыт")  # Ош

    def test_conflicting_school_and_city_is_empty(self):
        school = next(s for s in self.repo.schools() if "Ошский" in s.name)
        r = self.client.get(reverse("home"), {"school": school.slug, "city": "Бишкек"})
        self.assertContains(r, "Ничего не нашлось")

    def test_sort_by_reviews_ignores_invalid_and_keeps_rating(self):
        self.assertEqual(
            self.client.get(reverse("home"), {"sort": "nonsense"}).context["sort"], "rating"
        )


# ---------------------------------------------------------------------------
# Form validation edge cases: empty, overlong, invalid rating
# ---------------------------------------------------------------------------
class ReviewFormValidationEdgeTests(TestCase):
    def test_empty_form_reports_required_fields(self):
        form = ReviewForm({})
        self.assertFalse(form.is_valid())
        for field in ("course", "overall", "difficulty", "would_take_again", "comment"):
            self.assertIn(field, form.errors)

    def test_invalid_overall_ratings_are_rejected(self):
        for value in ("", "0", "6", "abc", "4.5", "-1"):
            with self.subTest(overall=value):
                form = ReviewForm(_valid({"overall": value}))
                self.assertFalse(form.is_valid())
                self.assertIn("overall", form.errors)

    def test_invalid_difficulty_ratings_are_rejected(self):
        for value in ("", "0", "6", "abc"):
            with self.subTest(difficulty=value):
                form = ReviewForm(_valid({"difficulty": value}))
                self.assertFalse(form.is_valid())
                self.assertIn("difficulty", form.errors)

    def test_boundary_ratings_are_accepted(self):
        for value in ("1", "5"):
            with self.subTest(overall=value):
                form = ReviewForm(_valid({"overall": value, "difficulty": value}))
                self.assertTrue(form.is_valid(), form.errors)

    def test_whitespace_only_course_is_rejected(self):
        form = ReviewForm(_valid({"course": "   "}))
        self.assertFalse(form.is_valid())
        self.assertIn("course", form.errors)

    def test_overlong_course_and_term_are_rejected(self):
        form = ReviewForm(_valid({"course": "я" * 81}))
        self.assertFalse(form.is_valid())
        self.assertIn("course", form.errors)
        form2 = ReviewForm(_valid({"term": "я" * 21}))
        self.assertFalse(form2.is_valid())
        self.assertIn("term", form2.errors)

    def test_overlong_comment_is_rejected(self):
        form = ReviewForm(_valid({"comment": "я" * 1001}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_boundary_lengths_are_accepted(self):
        form = ReviewForm(_valid({"course": "я" * 80, "term": "я" * 20, "comment": "я" * 1000}))
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_enum_choices_are_rejected(self):
        for overrides in ({"grade": "Z"}, {"would_take_again": "maybe"}, {"tags": ["Нет такого тега"]}):
            with self.subTest(**overrides):
                form = ReviewForm(_valid(overrides))
                self.assertFalse(form.is_valid())
                self.assertIn(list(overrides)[0], form.errors)


class ProfessorFormValidationEdgeTests(TestCase):
    class _School:
        def __init__(self, id, name):
            self.id = id
            self.name = name

    def _form(self, data):
        from .forms import ProfessorForm
        return ProfessorForm(data, schools=[self._School("1", "Вуз")])

    def test_empty_form_reports_required_fields(self):
        form = self._form({})
        self.assertFalse(form.is_valid())
        for field in ("school", "first_name", "last_name", "department", "title"):
            self.assertIn(field, form.errors)

    def test_invalid_school_is_rejected(self):
        form = self._form({"school": "999", "first_name": "Иван", "last_name": "Петров",
                           "department": "Физика", "title": "доцент"})
        self.assertFalse(form.is_valid())
        self.assertIn("school", form.errors)

    def test_invalid_title_is_rejected(self):
        form = self._form({"school": "1", "first_name": "Иван", "last_name": "Петров",
                           "department": "Физика", "title": "ректор"})
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_overlong_names_and_department_are_rejected(self):
        form = self._form({"school": "1", "first_name": "я" * 41, "last_name": "Петров",
                           "department": "Физика", "title": "доцент"})
        self.assertFalse(form.is_valid())
        self.assertIn("first_name", form.errors)
        form2 = self._form({"school": "1", "first_name": "Иван", "last_name": "Петров",
                            "department": "я" * 81, "title": "доцент"})
        self.assertFalse(form2.is_valid())
        self.assertIn("department", form2.errors)


class ReportFormValidationTests(TestCase):
    def test_reason_is_bounded_by_model_max_length(self):
        from .forms import ReportForm
        from .models import Report

        max_length = Report._meta.get_field("reason").max_length
        valid = ReportForm({"reason": "spam"})
        self.assertTrue(valid.is_valid(), valid.errors)

        oversized = ReportForm({"reason": "x" * (max_length + 1)})
        self.assertFalse(oversized.is_valid())
        self.assertIn("reason", oversized.errors)
        self.assertEqual(ReportForm().fields["reason"].widget.attrs["maxlength"], max_length)
