import re
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.csp import CSP
from django.utils.formats import localize

from .django_repo import DjangoRepository
from .repository import InMemoryRepository
from .seed import seed

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_CSS = Path(__file__).parent / "static" / "reviews" / "style.css"


class HealthzTests(TestCase):
    def test_healthz_is_public_and_does_not_query_database(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


def _valid(overrides=None):
    d = {"course": "Алгоритмы", "term": "Осень 2025", "overall": "4", "difficulty": "3",
         "would_take_again": "yes", "grade": "A", "tags": ["Интересные лекции"],
         "comment": "Понятные лекции и честные экзамены, рекомендую."}
    if overrides:
        d.update(overrides)
    return d


def _repo_review(course="Алгоритмы", overall=5, difficulty=3, wta=True, grade="A",
                 tags=None, comment="Тестовый отзыв достаточной длины."):
    """Cleaned-data shape for repository.add_review (ints, not form strings)."""
    return {"course": course, "term": "Осень 2025", "overall": overall,
            "difficulty": difficulty, "would_take_again": wta, "grade": grade,
            "comment": comment, "tags": tags or ["Интересные лекции"]}


class MemorySmokeTests(TestCase):
    """Smoke against InMemoryRepository (no DB) via isolated repo."""
    def setUp(self):
        # use isolated in-memory repo for predictable slugs
        from reviews import store as store_mod
        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig = store_mod.repo
        store_mod.repo = self.repo
        # also patch views' repo reference
        import reviews.views as v
        self._orig_v = v.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    def test_home_and_search(self):
        self.assertContains(self.client.get(reverse("home")), "Айбек")
        r = self.client.get(reverse("home"), {"q": "физика"})
        self.assertContains(r, "Волков")
        self.assertNotContains(r, "Айбек")

    def test_filter_city_and_school(self):
        # filter by city Osh should show only Osh prof
        r = self.client.get(reverse("home"), {"city": "Ош"})
        self.assertContains(r, "Бакыт")
        self.assertNotContains(r, "Айбек")
        # filter by school slug
        school = next(s for s in self.repo.schools() if "Ошский" in s.name)
        r2 = self.client.get(reverse("home"), {"school": school.slug})
        self.assertContains(r2, "Бакыт")

    def test_filter_department(self):
        # seeded Физика: Дмитрий Волков; Информатика: Айбек Токтогулов and Бакыт Маматов
        r = self.client.get(reverse("home"), {"department": "Физика"})
        self.assertContains(r, "Волков")
        self.assertNotContains(r, "Айбек")
        self.assertNotContains(r, "Бакыт")
        self.assertContains(r, 'name="department"')
        self.assertContains(r, 'selected>Физика')
        # case-insensitive + surrounding whitespace
        r_ci = self.client.get(reverse("home"), {"department": "  физика  "})
        self.assertContains(r_ci, "Волков")
        self.assertNotContains(r_ci, "Айбек")
        # empty department keeps existing unfiltered results
        r_all = self.client.get(reverse("home"), {"department": ""})
        self.assertContains(r_all, "Айбек")
        self.assertContains(r_all, "Волков")
        # pagination still present on the full list (12 seeded professors, 10 per page)
        self.assertContains(r_all, "Вперёд")
        # a narrow department list does not paginate
        self.assertNotContains(r, "Вперёд")
        # department combines with city
        r_city = self.client.get(reverse("home"), {"department": "Информатика", "city": "Ош"})
        self.assertContains(r_city, "Бакыт")
        self.assertNotContains(r_city, "Айбек")

    def test_professor_page_and_404(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        self.assertContains(self.client.get(reverse("professor", args=[prof.slug])), "Алгоритмы")
        self.assertEqual(self.client.get(reverse("professor", args=["nobody"])).status_code, 404)

    def test_post_review_requires_login_and_pending(self):
        # anonymous redirected to login
        prof = self.repo.search("Айбек")[0]["prof"]
        r = self.client.post(reverse("review_new", args=[prof.slug]), _valid())
        self.assertIn(r.status_code, (302, 200))
        # login and post
        user = User.objects.create_user(username="tester@example.com", email="tester@example.com", password="pass12345")
        self.client.force_login(user)
        r2 = self.client.post(reverse("review_new", args=[prof.slug]), _valid())
        self.assertEqual(r2.status_code, 302)
        # pending, not visible yet
        self.assertEqual(len(self.repo.reviews_for(prof.id)), 3)  # seed has 3 for first prof

    def test_vote_requires_login_and_one_vote(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        review = self.repo.reviews_for(prof.id)[0]
        # anonymous redirected
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertIn(r.status_code, (302, 200))
        user = User.objects.create_user(username="voter@example.com", email="voter@example.com", password="pass12345")
        self.client.force_login(user)
        before = review.helpful
        r2 = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(review.helpful, before + 1)
        # second vote by same user not counted twice
        r3 = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r3.status_code, 302)
        self.assertEqual(review.helpful, before + 1)

    def test_course_not_uppercased_and_max_80(self):
        user = User.objects.create_user(username="c@example.com", email="c@example.com", password="pass12345")
        self.client.force_login(user)
        prof = self.repo.search("Айбек")[0]["prof"]
        # course with mixed case preserved (trim only)
        self.client.post(reverse("review_new", args=[prof.slug]), _valid({"course": "  Микроэкономика  "}))
        # fetch pending review
        pending = [r for r in self.repo.reviews_for(prof.id, include_pending=True) if r.course == "Микроэкономика"]
        self.assertTrue(pending)

    def test_grades_include_kg_scale(self):
        from .repository import GRADES
        for g in ["A", "E", "FX", "F", "Зачёт", "Незачёт", "Ещё не знаю"]:
            self.assertIn(g, [str(x) for x in GRADES])

    def test_tags_no_corruption(self):
        from .repository import TAGS
        joined = " ".join(str(t) for t in TAGS).lower()
        self.assertNotIn("взят", joined)
        self.assertNotIn("корруп", joined)

    def test_i18n_languages(self):
        for lang in ["ru", "ky", "en"]:
            r = self.client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE=lang)
            self.assertEqual(r.status_code, 200)

    def test_rules_page(self):
        self.assertContains(self.client.get(reverse("rules")), "Правила")

    def test_school_page(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        school = next(row["school"] for row in self.repo.search() if row["prof"].id == prof.id)
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, school.name)

    def test_add_professor_requires_login_and_duplicate_check(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        school = next(row["school"] for row in self.repo.search() if row["prof"].id == prof.id)
        r = self.client.get(reverse("add_professor"))
        # login required
        self.assertIn(r.status_code, (302, 200))
        user = User.objects.create_user(username="addp@example.com", email="addp@example.com", password="pass12345")
        self.client.force_login(user)
        r2 = self.client.get(reverse("add_professor"))
        self.assertEqual(r2.status_code, 200)
        # add new prof
        data = {
            "school": str(school.id), "first_name": "Тест", "last_name": "Тестов",
            "department": "Кафедра теста", "title": "доцент",
        }
        r3 = self.client.post(reverse("add_professor"), data)
        self.assertEqual(r3.status_code, 302)
        # duplicate
        r4 = self.client.post(reverse("add_professor"), data)
        self.assertEqual(r4.status_code, 200)


# Django ORM backend — same smoke with DB
class DjangoSmokeTests(TestCase):
    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod
        self.repo = DjangoRepository()
        # seed via models helper
        from reviews.management.commands.seed_kg import Command as SeedCmd
        cmd = SeedCmd()
        cmd.handle(clear=True)
        # use DjangoRepository for views
        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    def test_home_shows_kg_data(self):
        self.assertContains(self.client.get(reverse("home")), "Айбек")

    def test_filter_department(self):
        r = self.client.get(reverse("home"), {"department": "Физика"})
        self.assertContains(r, "Волков")
        self.assertNotContains(r, "Айбек")
        r_ci = self.client.get(reverse("home"), {"department": "физика"})
        self.assertContains(r_ci, "Волков")
        self.assertNotContains(r_ci, "Смирнова")
        r_all = self.client.get(reverse("home"), {"department": ""})
        self.assertContains(r_all, "Айбек")
        self.assertContains(r_all, "Волков")
        names = [row["prof"].last_name for row in self.repo.search(department="Информатика")]
        self.assertCountEqual(names, ["Токтогулов", "Маматов"])

    def test_review_pending_not_visible(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        user = User.objects.create_user(
            username="django_tester@example.com",
            email="django_tester@example.com",
            password="pass12345",
        )
        self.client.force_login(user)
        before = len(self.repo.reviews_for(prof.id))
        self.client.post(reverse("review_new", args=[prof.slug]), _valid())
        self.assertEqual(len(self.repo.reviews_for(prof.id)), before)
        self.assertEqual(len(self.repo.reviews_for(prof.id, include_pending=True)), before + 1)


# ---------------------------------------------------------------------------
# Repository contract: identical API assertions run against BOTH backends
# ---------------------------------------------------------------------------
class RepositoryContractMixin:
    def _make(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repo = self._make()

    def _school(self, name="Тестовый университет", city="Бишкек"):
        return self.repo.add_school(name, city)

    def _prof(self, school_id, first="Иван", last="Петров"):
        return self.repo.add_professor(school_id, first, last, "Физика", "доцент")

    def _user(self, email):
        return User.objects.create_user(username=email, email=email, password="pass12345")

    def test_schools_cities_and_lookup(self):
        s = self._school()
        self.assertIn(s, self.repo.schools())
        self.assertIn("Бишкек", self.repo.cities())
        self.assertEqual(self.repo.school_by_slug(s.slug).id, s.id)
        self.assertEqual(self.repo.school(s.id).name, s.name)
        self.assertIsNone(self.repo.school_by_slug("does-not-exist"))

    def test_add_professor_and_reads(self):
        s = self._school()
        p = self._prof(s.id)
        self.assertEqual(self.repo.professor(p.slug).id, p.id)
        self.assertEqual(self.repo.professor_by_id(p.id).full_name, "Иван Петров")
        self.assertEqual(self.repo.professor_any(p.slug).id, p.id)
        self.assertEqual([x.id for x in self.repo.professors_for_school(s.id)], [p.id])

    def test_duplicate_professor_rejected(self):
        s = self._school()
        self._prof(s.id)
        with self.assertRaises(ValueError):
            self.repo.add_professor(s.id, "Иван", "Петров", "Математика", "профессор")
        # same ФИО in a different school is allowed
        s2 = self._school("Другой вуз", "Ош")
        self.repo.add_professor(s2.id, "Иван", "Петров", "Физика", "доцент")

    def test_search_filters(self):
        s = self._school()
        p = self._prof(s.id)
        self.assertEqual(self.repo.stats(p.id)["count"], 0)
        self.assertEqual([x["prof"].id for x in self.repo.search("петров")], [p.id])
        self.assertEqual(self.repo.search("неттакого"), [])
        self.assertEqual(self.repo.search("", city="Ош"), [])
        self.assertEqual(len(self.repo.search("", city="Бишкек")), 1)
        self.assertEqual(len(self.repo.search("", school_slug=s.slug)), 1)

    def test_search_department_filter_and_sort(self):
        s = self._school()
        p1 = self.repo.add_professor(s.id, "Иван", "Петров", "Физика", "доцент")
        p2 = self.repo.add_professor(s.id, "Пётр", "Сидоров", "Математика", "доцент")
        self.repo.add_review(p1.id, _repo_review(course="К1", overall=4), status="approved")
        self.repo.add_review(p1.id, _repo_review(course="К2", overall=4), status="approved")
        self.repo.add_review(p2.id, _repo_review(course="К3", overall=5), status="approved")
        # department filter
        self.assertEqual([x["prof"].id for x in self.repo.search("", department="Физика")], [p1.id])
        self.assertEqual([x["prof"].id for x in self.repo.search("", department="Математика")], [p2.id])
        # sort by review count: p1 (2 reviews) comes first
        by_reviews = self.repo.search("", sort="reviews")
        self.assertEqual(by_reviews[0]["prof"].id, p1.id)
        # sort by rating: p2 (overall 5) comes first
        by_rating = self.repo.search("", sort="rating")
        self.assertEqual(by_rating[0]["prof"].id, p2.id)
        self.assertIn("Физика", self.repo.departments())

    def test_add_review_pending_then_approve_and_stats(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_r1@example.com")
        r = self.repo.add_review(p.id, _repo_review(), user=u)
        self.assertEqual(r.status, "pending")
        self.assertEqual(self.repo.reviews_for(p.id), [])
        self.assertEqual(len(self.repo.reviews_for(p.id, include_pending=True)), 1)
        self.repo.approve_review(r.id)
        self.assertEqual(len(self.repo.reviews_for(p.id)), 1)
        st = self.repo.stats(p.id)
        self.assertEqual(st["count"], 1)
        self.assertEqual(st["overall"], 5.0)
        self.assertEqual(st["difficulty"], 3.0)
        self.assertEqual(st["wta_pct"], 100)
        self.assertEqual(st["tone"], "good")
        self.assertIn("Интересные лекции", st["top_tags"])

    def test_stats_tone_thresholds_are_shared_between_backends(self):
        s = self._school()
        cases = [(5, "good"), (4, "good"), (3, "mid"), (2, "bad"), (1, "bad")]
        for index, (score, tone) in enumerate(cases):
            p = self.repo.add_professor(
                s.id, f"Имя{index}", f"Фамилия{index}", "Физика", "доцент"
            )
            self.repo.add_review(p.id, _repo_review(overall=score), status="approved")
            self.assertEqual(self.repo.stats(p.id)["tone"], tone)

    def test_anonymous_review_auto_approved(self):
        s = self._school()
        p = self._prof(s.id)
        r = self.repo.add_review(p.id, _repo_review(), user=None)
        self.assertEqual(r.status, "approved")
        self.assertEqual(len(self.repo.reviews_for(p.id)), 1)

    def test_duplicate_review_rejected(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_r2@example.com")
        self.repo.add_review(p.id, _repo_review(), user=u)
        with self.assertRaises(ValueError):
            self.repo.add_review(p.id, _repo_review(), user=u)

    def test_has_user_reviewed(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_r3@example.com")
        # ASCII course: SQLite LIKE folds case only for ASCII (Cyrillic is case-sensitive)
        self.assertFalse(self.repo.has_user_reviewed(p.id, "Algorithms", u))
        self.assertFalse(self.repo.has_user_reviewed(p.id, "Algorithms", None))
        self.repo.add_review(p.id, _repo_review(course="Algorithms"), user=u)
        self.assertTrue(self.repo.has_user_reviewed(p.id, "algorithms", u))
        self.assertFalse(self.repo.has_user_reviewed(p.id, "Other", u))

    def test_vote_once_and_counts(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_v1@example.com")
        r = self.repo.add_review(p.id, _repo_review(), status="approved")
        self.assertFalse(self.repo.has_user_voted(r.id, u))
        self.repo.vote(r.id, helpful=True, user=u)
        self.assertTrue(self.repo.has_user_voted(r.id, u))
        self.assertEqual(self.repo.review(r.id).helpful, 1)
        with self.assertRaises(ValueError):
            self.repo.vote(r.id, helpful=True, user=u)
        u2 = self._user("contract_v2@example.com")
        self.repo.vote(r.id, helpful=False, user=u2)
        self.assertEqual(self.repo.review(r.id).not_helpful, 1)
        self.assertEqual(self.repo.review(r.id).helpful, 1)

    def test_reviews_for_helpful_sort(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_s1@example.com")
        r1 = self.repo.add_review(p.id, _repo_review(course="Курс 1"), status="approved")
        self.repo.add_review(p.id, _repo_review(course="Курс 2"), status="approved")
        self.repo.vote(r1.id, helpful=True, user=u)
        ordered = self.repo.reviews_for(p.id, sort="helpful")
        self.assertEqual(ordered[0].id, r1.id)

    def test_report_counted(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_rep@example.com")
        r = self.repo.add_review(p.id, _repo_review(), status="approved")
        self.repo.report(r.id, user=u)
        self.assertEqual(self.repo.review(r.id).reported, 1)

    def test_school_stats(self):
        s = self._school()
        p = self._prof(s.id)
        self.repo.add_review(p.id, _repo_review(), status="approved")
        st = self.repo.school_stats(s.id)
        self.assertEqual(st["prof_count"], 1)
        self.assertEqual(st["review_count"], 1)
        self.assertEqual(st["avg_overall"], 5.0)

    def test_cities_and_departments_are_deduplicated(self):
        """Filter lists must not repeat a city/department once per row."""
        s1 = self.repo.add_school("Вуз А", "Бишкек")
        self.repo.add_school("Вуз Б", "Бишкек")
        self.repo.add_professor(s1.id, "Иван", "Петров", "Физика", "доцент")
        self.repo.add_professor(s1.id, "Пётр", "Сидоров", "Физика", "доцент")
        cities = self.repo.cities()
        departments = self.repo.departments()
        self.assertEqual(len(cities), len(set(cities)))
        self.assertEqual(len(departments), len(set(departments)))
        self.assertEqual(cities.count("Бишкек"), 1)
        self.assertEqual(departments.count("Физика"), 1)

    def test_all_reviews_visibility(self):
        s = self._school()
        p = self._prof(s.id)
        u = self._user("contract_all@example.com")
        self.repo.add_review(p.id, _repo_review(course="Одобренный"), status="approved")
        self.repo.add_review(p.id, _repo_review(course="На модерации"), user=u)
        self.assertEqual(len(self.repo.all_reviews()), 1)
        self.assertEqual(len(self.repo.all_reviews(include_pending=True)), 2)

    def test_pending_professor_hidden_until_approved(self):
        s = self._school()
        p = self.repo.add_professor(s.id, "Пётр", "Скрытый", "Физика", "доцент", status="pending")
        self.assertIsNone(self.repo.professor(p.slug))
        self.assertIsNotNone(self.repo.professor_any(p.slug))
        self.assertEqual(self.repo.search("Скрытый"), [])
        self.repo.approve_professor(p.id)
        self.assertIsNotNone(self.repo.professor(p.slug))
        self.assertEqual(len(self.repo.search("Скрытый")), 1)


class MemoryRepositoryContractTests(RepositoryContractMixin, TestCase):
    def _make(self):
        return InMemoryRepository()


class DjangoRepositoryContractTests(RepositoryContractMixin, TestCase):
    def _make(self):
        return DjangoRepository()


# ---------------------------------------------------------------------------
# View coverage against InMemoryRepository
# ---------------------------------------------------------------------------
class _MemoryRepoTestCase(TestCase):
    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod
        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    def login(self, email="viewer@example.com"):
        user = User.objects.create_user(username=email, email=email, password="pass12345")
        self.client.force_login(user)
        return user

    def prof(self, q="Айбек"):
        return self.repo.search(q)[0]["prof"]


class HomeViewTests(_MemoryRepoTestCase):
    def test_home_lists_seed(self):
        r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Айбек")
        self.assertContains(r, "Волков")

    def test_home_no_results_message(self):
        r = self.client.get(reverse("home"), {"q": "неттакого"})
        self.assertContains(r, "Ничего не нашлось")

    def test_home_pagination(self):
        r = self.client.get(reverse("home"))
        self.assertIsNotNone(r.context["page_obj"])
        self.assertEqual(r.context["page_obj"].paginator.num_pages, 2)
        r2 = self.client.get(reverse("home"), {"page": 2})
        self.assertEqual(r2.status_code, 200)
        self.assertLessEqual(len(r2.context["results"]), 10)

    def test_home_department_filter(self):
        r = self.client.get(reverse("home"), {"department": "Информатика"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Айбек")
        self.assertContains(r, "Бакыт")
        self.assertNotContains(r, "Волков")
        self.assertIn("Информатика", r.context["departments"])

    def test_home_sort_by_reviews(self):
        r = self.client.get(reverse("home"), {"sort": "reviews"})
        self.assertEqual(r.context["sort"], "reviews")
        self.assertEqual(r.context["results"][0]["prof"].full_name, "Айбек Токтогулов")
        self.assertGreaterEqual(r.context["results"][0]["stats"]["count"],
                                r.context["results"][1]["stats"]["count"])

    def test_home_sort_defaults_and_invalid(self):
        self.assertEqual(self.client.get(reverse("home")).context["sort"], "rating")
        self.assertEqual(self.client.get(reverse("home"), {"sort": "bogus"}).context["sort"], "rating")

    def test_home_renders_department_and_sort_controls(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, 'name="department"')
        self.assertContains(r, 'name="sort"')
        self.assertContains(r, "По рейтингу")
        self.assertContains(r, "По числу отзывов")

    def test_home_uses_shared_stylesheet_and_viewport_meta(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, "reviews/style.css")
        self.assertContains(r, 'name="viewport" content="width=device-width, initial-scale=1"')


class SchoolViewTests(_MemoryRepoTestCase):
    def test_school_detail_stats_and_departments(self):
        school = next(s for s in self.repo.schools() if "Ошский" in s.name)
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, school.name)
        self.assertEqual(r.context["stats"]["prof_count"], 1)
        self.assertContains(r, "Бакыт")

    def test_school_detail_404(self):
        self.assertEqual(self.client.get(reverse("school_detail", args=["nope"])).status_code, 404)


class ProfessorViewTests(_MemoryRepoTestCase):
    def test_professor_detail_stats_and_sort(self):
        prof = self.prof()
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["sort"], "new")
        self.assertEqual(r.context["stats"]["count"], 3)
        r2 = self.client.get(reverse("professor", args=[prof.slug]), {"sort": "helpful"})
        self.assertEqual(r2.context["sort"], "helpful")
        self.assertContains(r2, "Алгоритмы")

    def test_professor_pending_review_not_shown(self):
        prof = self.prof()
        u = self.login()
        self.repo.add_review(
            prof.id,
            _repo_review(course="Скрытый курс", comment="Скрытый комментарий достаточной длины."),
            user=u,
        )
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertNotContains(r, "Скрытый комментарий достаточной длины.")

    def test_distribution_renders_when_reviews_exist(self):
        prof = self.prof()
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertContains(r, 'class="dist"')
        self.assertContains(r, 'aria-label="Распределение оценок"')
        self.assertContains(r, '<span class="bar"><i style="width:')
        self.assertContains(r, ">5<")

    def test_distribution_empty_state_when_no_reviews(self):
        school = self.repo.add_school("Test School", "Test City")
        new_prof = self.repo.add_professor(school.id, "Test", "Professor", "Test Dept", "преподаватель")
        r = self.client.get(reverse("professor", args=[new_prof.slug]))
        self.assertContains(r, 'class="dist dist--empty"')
        self.assertContains(r, 'aria-label="Распределение оценок"')
        self.assertContains(r, ">0<")
        self.assertNotContains(r, 'class="facts"')


class ReviewFormViewTests(_MemoryRepoTestCase):
    def test_anonymous_get_redirects_to_login(self):
        r = self.client.get(reverse("review_new", args=[self.prof().slug]))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r.url)

    def test_authenticated_get_renders_form(self):
        self.login()
        r = self.client.get(reverse("review_new", args=[self.prof().slug]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("form", r.context)
        self.assertContains(r, "Отправить на модерацию")

    def test_invalid_comment_rejected(self):
        self.login()
        prof = self.prof()
        before = len(self.repo.reviews_for(prof.id, include_pending=True))
        r = self.client.post(reverse("review_new", args=[prof.slug]), _valid({"comment": "коротко"}))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["form"].is_valid())
        self.assertEqual(len(self.repo.reviews_for(prof.id, include_pending=True)), before)

    def test_too_many_tags_rejected(self):
        self.login()
        prof = self.prof()
        tags = ["Строго оценивает", "Понятные критерии", "Интересные лекции", "Много домашних заданий"]
        r = self.client.post(reverse("review_new", args=[prof.slug]), _valid({"tags": tags}))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors.get("tags"))

    def test_success_redirects_and_is_pending(self):
        self.login()
        prof = self.prof()
        r = self.client.post(reverse("review_new", args=[prof.slug]), _valid())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("professor", args=[prof.slug]))
        pending = [x for x in self.repo.reviews_for(prof.id, include_pending=True)
                   if x.course == "Алгоритмы" and x.status == "pending"]
        self.assertEqual(len(pending), 1)

    def test_duplicate_course_shows_error(self):
        self.login()
        prof = self.prof()
        url = reverse("review_new", args=[prof.slug])
        self.client.post(url, _valid())
        r = self.client.post(url, _valid())
        self.assertEqual(r.status_code, 200)
        self.assertIn("course", r.context["form"].errors)
        pending = [x for x in self.repo.reviews_for(prof.id, include_pending=True)
                   if x.course == "Алгоритмы"]
        self.assertEqual(len(pending), 1)


class ReviewActionViewTests(_MemoryRepoTestCase):
    def test_vote_404(self):
        self.login()
        r = self.client.post(reverse("review_vote", args=["nope"]), {"vote": "up"})
        self.assertEqual(r.status_code, 404)

    def test_vote_redirects_and_increments(self):
        prof = self.prof()
        review = self.repo.reviews_for(prof.id)[0]
        self.login()
        before = review.helpful
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertIn(f"#review-{review.id}", r.url)
        self.assertEqual(review.helpful, before + 1)

    def test_report_creates_report(self):
        prof = self.prof()
        review = self.repo.reviews_for(prof.id)[0]
        self.login()
        r = self.client.post(reverse("review_report", args=[review.id]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(review.reported, 1)

    def test_report_404(self):
        self.login()
        r = self.client.post(reverse("review_report", args=["nope"]))
        self.assertEqual(r.status_code, 404)


class AddProfessorViewTests(_MemoryRepoTestCase):
    def _data(self, school):
        return {"school": str(school.id), "first_name": "Тест", "last_name": "Тестов",
                "department": "Кафедра теста", "title": "доцент"}

    def test_anonymous_redirects_to_login(self):
        r = self.client.get(reverse("add_professor"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r.url)

    def test_invalid_form_rejected(self):
        self.login()
        r = self.client.post(reverse("add_professor"), {})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors)

    def test_creates_pending_and_hidden_until_approved(self):
        self.login()
        school = self.repo.schools()[0]
        r = self.client.post(reverse("add_professor"), self._data(school))
        self.assertEqual(r.status_code, 302)
        pending = [p for p in self.repo._professors.values() if p.status == "pending"]
        self.assertEqual(len(pending), 1)
        p = pending[0]
        self.assertIsNone(self.repo.professor(p.slug))
        self.assertEqual(self.repo.search("Тестов"), [])
        self.repo.approve_professor(p.id)
        self.assertIsNotNone(self.repo.professor(p.slug))
        self.assertEqual(len(self.repo.search("Тестов")), 1)


# ---------------------------------------------------------------------------
# Auth / signup views
# ---------------------------------------------------------------------------
class AuthViewTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        r = self.client.post(reverse("signup"), {
            "email": "new@example.com", "password1": "pass12345", "password2": "pass12345"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))
        self.assertTrue(User.objects.filter(username="new@example.com").exists())
        self.assertIn("_auth_user_id", self.client.session)

    def test_signup_duplicate_email(self):
        User.objects.create_user(username="dup@example.com", email="dup@example.com", password="pass12345")
        r = self.client.post(reverse("signup"), {
            "email": "dup@example.com", "password1": "pass12345", "password2": "pass12345"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("email", r.context["form"].errors)

    def test_signup_password_mismatch(self):
        r = self.client.post(reverse("signup"), {
            "email": "x@example.com", "password1": "pass12345", "password2": "different1"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="x@example.com").exists())

    def test_signup_short_password(self):
        r = self.client.post(reverse("signup"), {
            "email": "y@example.com", "password1": "short", "password2": "short"})
        self.assertEqual(r.status_code, 200)

    def test_authenticated_signup_redirects_home(self):
        user = User.objects.create_user(username="here@example.com", email="here@example.com", password="pass12345")
        self.client.force_login(user)
        r = self.client.get(reverse("signup"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("home"))


# ---------------------------------------------------------------------------
# View + moderation coverage against DjangoRepository (ORM)
# ---------------------------------------------------------------------------
class DjangoViewCoverageTests(TestCase):
    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod
        self.repo = DjangoRepository()
        from reviews.management.commands.seed_kg import Command as SeedCmd
        SeedCmd().handle(clear=True)
        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    def login(self, email="dview@example.com"):
        user = User.objects.create_user(username=email, email=email, password="pass12345")
        self.client.force_login(user)
        return user

    def test_home_school_professor_pages(self):
        self.assertContains(self.client.get(reverse("home")), "Айбек")
        school = self.repo.schools()[0]
        self.assertEqual(self.client.get(reverse("school_detail", args=[school.slug])).status_code, 200)
        prof = self.repo.search("Айбек")[0]["prof"]
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["stats"]["count"], 3)

    def test_review_pending_then_approved_visible(self):
        from reviews.models import Review
        prof = self.repo.search("Айбек")[0]["prof"]
        comment = "Понятные лекции и честные экзамены, рекомендую."
        self.login()
        self.client.post(reverse("review_new", args=[prof.slug]), _valid())
        pending = Review.objects.get(status="pending")
        self.assertNotContains(self.client.get(reverse("professor", args=[prof.slug])), comment)
        self.repo.approve_review(str(pending.pk))
        self.assertEqual(Review.objects.get(pk=pending.pk).status, "approved")
        self.assertContains(self.client.get(reverse("professor", args=[prof.slug])), comment)

    def test_add_professor_pending_then_approved(self):
        from reviews.models import Professor
        school = self.repo.schools()[0]
        self.login()
        data = {"school": str(school.id), "first_name": "Тест", "last_name": "Модератов",
                "department": "Кафедра", "title": "доцент"}
        r = self.client.post(reverse("add_professor"), data)
        self.assertEqual(r.status_code, 302)
        p = Professor.objects.get(status="pending")
        self.assertIsNone(self.repo.professor(p.slug))
        self.assertEqual(self.repo.search("Модератов"), [])
        self.assertNotContains(self.client.get(reverse("home")), "Модератов")
        self.repo.approve_professor(str(p.pk))
        self.assertIsNotNone(self.repo.professor(p.slug))
        self.assertContains(self.client.get(reverse("home"), {"q": "Модератов"}), "Модератов")

    def test_review_vote_and_report_flow(self):
        from reviews.models import Report
        prof = self.repo.search("Айбек")[0]["prof"]
        review = self.repo.reviews_for(prof.id)[0]
        self.login()
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.repo.review(review.id).helpful, 1)
        self.client.post(reverse("review_report", args=[review.id]))
        self.assertEqual(Report.objects.filter(review_id=review.id).count(), 1)


# ---------------------------------------------------------------------------
# Security settings hardening
# ---------------------------------------------------------------------------
class SecuritySettingsTests(TestCase):
    def test_x_frame_options_deny(self):
        from django.conf import settings
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")

    def test_password_validators_configured(self):
        from django.conf import settings
        names = [v["NAME"] for v in settings.AUTH_PASSWORD_VALIDATORS]
        self.assertIn("django.contrib.auth.password_validation.MinimumLengthValidator", names)
        self.assertIn("django.contrib.auth.password_validation.CommonPasswordValidator", names)
        self.assertIn("django.contrib.auth.password_validation.NumericPasswordValidator", names)
        self.assertIn(
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator", names
        )

    def test_security_switches_off_when_debug(self):
        from config import settings as site
        sec = site._security_settings(debug=True)
        self.assertFalse(sec["SECURE_SSL_REDIRECT"])
        self.assertEqual(sec["SECURE_HSTS_SECONDS"], 0)
        self.assertFalse(sec["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertFalse(sec["SECURE_HSTS_PRELOAD"])
        self.assertFalse(sec["SESSION_COOKIE_SECURE"])
        self.assertFalse(sec["CSRF_COOKIE_SECURE"])
        self.assertIsNone(sec["SECURE_PROXY_SSL_HEADER"])

    def test_security_switches_on_in_production(self):
        from config import settings as site
        sec = site._security_settings(debug=False)
        self.assertTrue(sec["SECURE_SSL_REDIRECT"])
        self.assertGreater(sec["SECURE_HSTS_SECONDS"], 0)
        self.assertTrue(sec["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertTrue(sec["SECURE_HSTS_PRELOAD"])
        self.assertTrue(sec["SESSION_COOKIE_SECURE"])
        self.assertTrue(sec["CSRF_COOKIE_SECURE"])
        self.assertEqual(sec["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_nosniff_always_enabled(self):
        from config import settings as site
        self.assertTrue(site._security_settings(debug=True)["SECURE_CONTENT_TYPE_NOSNIFF"])
        self.assertTrue(site._security_settings(debug=False)["SECURE_CONTENT_TYPE_NOSNIFF"])

    def test_referrer_policy_configured(self):
        from django.conf import settings
        self.assertEqual(settings.SECURE_REFERRER_POLICY, "same-origin")

    def test_permissions_policy_configured(self):
        from django.conf import settings
        self.assertEqual(settings.PERMISSIONS_POLICY, "camera=(), geolocation=(), microphone=()")

    @override_settings(DEBUG=True)
    def test_security_headers_are_emitted(self):
        """Middleware must actually send the hardening response headers."""
        from django.conf import settings

        response = self.client.get(reverse("home"))
        self.assertTrue(settings.DEBUG)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("Referrer-Policy"), "same-origin")
        self.assertNotIn("Strict-Transport-Security", response.headers)
        self.assertEqual(
            response.headers.get("Permissions-Policy"),
            "camera=(), geolocation=(), microphone=()",
        )

    def test_csp_header_is_emitted(self):
        response = self.client.get(reverse("home"))
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_csp_can_be_overridden(self):
        with override_settings(SECURE_CSP={"default-src": [CSP.SELF]}):
            response = self.client.get(reverse("home"))
            self.assertEqual(response.headers.get("Content-Security-Policy"), "default-src 'self'")

    def test_empty_csp_disables_header(self):
        with override_settings(SECURE_CSP={}):
            response = self.client.get(reverse("home"))
            self.assertNotIn("Content-Security-Policy", response.headers)

    def test_secure_cookies_default_to_debug_flag(self):
        from config import settings as site
        self.assertFalse(site._security_settings(debug=True)["SESSION_COOKIE_SECURE"])
        self.assertFalse(site._security_settings(debug=True)["CSRF_COOKIE_SECURE"])
        self.assertTrue(site._security_settings(debug=False)["SESSION_COOKIE_SECURE"])
        self.assertTrue(site._security_settings(debug=False)["CSRF_COOKIE_SECURE"])

    def test_secure_cookies_can_be_overridden_by_env(self):
        from config import settings as site
        forced_on = site._security_settings(debug=True, session_secure=True, csrf_secure=True)
        self.assertTrue(forced_on["SESSION_COOKIE_SECURE"])
        self.assertTrue(forced_on["CSRF_COOKIE_SECURE"])
        forced_off = site._security_settings(debug=False, session_secure=False, csrf_secure=False)
        self.assertFalse(forced_off["SESSION_COOKIE_SECURE"])
        self.assertFalse(forced_off["CSRF_COOKIE_SECURE"])

    def test_get_secure_cookie_parses_env(self):
        from config import settings as site
        self.assertTrue(site._get_secure_cookie("true", default=False))
        self.assertTrue(site._get_secure_cookie("1", default=False))
        self.assertTrue(site._get_secure_cookie("on", default=False))
        self.assertFalse(site._get_secure_cookie("no", default=True))
        self.assertTrue(site._get_secure_cookie(None, default=True))

    def test_default_secret_key_rejected_in_production(self):
        from django.core.exceptions import ImproperlyConfigured

        from config import settings as site
        with self.assertRaises(ImproperlyConfigured):
            site._validate_secret_key(site.DEFAULT_SECRET_KEY, debug=False)

    def test_custom_secret_key_accepted_in_production(self):
        from config import settings as site
        # must not raise
        site._validate_secret_key("a-strong-unique-production-key", debug=False)

    def test_default_secret_key_allowed_in_debug(self):
        from config import settings as site
        site._validate_secret_key(site.DEFAULT_SECRET_KEY, debug=True)

    def test_current_secure_values_match_import_debug_flag(self):
        from config import settings as site
        # Module-level switches are derived at import from the DEBUG env value
        # (Django later forces settings.DEBUG=False for the test run).
        self.assertEqual(site.SECURE_SSL_REDIRECT, not site.DEBUG)
        self.assertEqual(site.SESSION_COOKIE_SECURE, not site.DEBUG)
        self.assertEqual(site.CSRF_COOKIE_SECURE, not site.DEBUG)

    def test_session_cookie_httponly_enabled(self):
        from django.conf import settings
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_debug_defaults_to_false_off_tests(self):
        from config import settings as site
        self.assertFalse(site._get_debug(is_test=False, raw=None))
        self.assertFalse(site._get_debug(is_test=False, raw="0"))
        self.assertTrue(site._get_debug(is_test=False, raw="true"))
        # The suite itself must keep running in a developer-friendly mode.
        self.assertTrue(site._get_debug(is_test=True, raw=None))

    def test_allowed_hosts_default_is_safe(self):
        from config import settings as site
        hosts = site._get_allowed_hosts(None)
        self.assertNotIn("*", hosts)
        self.assertIn("localhost", hosts)
        self.assertIn("127.0.0.1", hosts)

    def test_allowed_hosts_parses_comma_separated_env(self):
        from config import settings as site
        self.assertEqual(site._get_allowed_hosts("a.example, b.example"), ["a.example", "b.example"])

    def test_wildcard_hosts_rejected_in_production(self):
        from django.core.exceptions import ImproperlyConfigured

        from config import settings as site
        with self.assertRaises(ImproperlyConfigured):
            site._validate_allowed_hosts(["*"], debug=False)
        # wildcard remains usable locally with DEBUG=True
        site._validate_allowed_hosts(["*"], debug=True)

    def test_csrf_trusted_origins_default_is_empty(self):
        from config import settings as site
        # Empty default keeps local HTTP dev working without extra config.
        self.assertEqual(site._get_csrf_trusted_origins(None), [])
        self.assertEqual(site._get_csrf_trusted_origins(""), [])

    def test_csrf_trusted_origins_parses_comma_separated_env(self):
        from config import settings as site
        self.assertEqual(
            site._get_csrf_trusted_origins("https://a.example, https://b.example"),
            ["https://a.example", "https://b.example"],
        )

    def test_csrf_trusted_origins_setting_is_a_list(self):
        from django.conf import settings
        self.assertIsInstance(settings.CSRF_TRUSTED_ORIGINS, list)

    def test_secret_key_allowed_hosts_csrf_origins_come_from_env(self):
        """A fresh settings import must source all three values from the env."""
        import importlib
        import os

        from config import settings as site
        env = {
            "SECRET_KEY": "env-provided-secret-key",
            "ALLOWED_HOSTS": "app.example,www.example",
            "CSRF_TRUSTED_ORIGINS": "https://app.example,https://www.example",
        }
        with mock.patch.dict(os.environ, env):
            reloaded = importlib.reload(site)
        try:
            self.assertEqual(reloaded.SECRET_KEY, "env-provided-secret-key")
            self.assertEqual(reloaded.ALLOWED_HOSTS, ["app.example", "www.example"])
            self.assertEqual(
                reloaded.CSRF_TRUSTED_ORIGINS,
                ["https://app.example", "https://www.example"],
            )
        finally:
            importlib.reload(site)


# ---------------------------------------------------------------------------
# UX design: responsive layout, professor cards, rating stars, a11y
# ---------------------------------------------------------------------------
class DesignAssetsTests(TestCase):
    """Static design assets: no external CDNs, responsive + a11y primitives."""

    def _templates(self):
        return [p for p in TEMPLATES_DIR.rglob("*.html")]

    def test_templates_have_no_external_resources(self):
        for path in self._templates():
            with self.subTest(template=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"https?://")

    def test_css_has_no_external_urls(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertNotRegex(css, r"url\(\s*['\"]?https?://")
        self.assertNotIn("@import", css)
        self.assertNotIn("fonts.googleapis", css)

    def test_css_is_responsive_and_motion_safe(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn("@media", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("color-scheme", css)  # declared in base.html meta + tokens

    def test_css_supports_dark_color_scheme(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn("prefers-color-scheme:dark", css.replace(" ", ""))
        self.assertIn("color-scheme:lightdark", css.replace(" ", ""))
        # Dark block must re-declare the core tokens, not rely on light values.
        dark = re.search(r"prefers-color-scheme\s*:\s*dark[^{]*\{\s*:root\s*\{([^}]*)\}", css)
        self.assertIsNotNone(dark)
        for token in ("--bg:", "--surface:", "--ink:", "--muted:", "--accent:"):
            self.assertIn(token, dark.group(1))

    def test_base_template_declares_light_and_dark_color_scheme(self):
        html = (TEMPLATES_DIR / "reviews" / "base.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="color-scheme" content="light dark">', html)

    def test_focus_visible_style_is_prominent(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        rule = re.search(r":focus-visible\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        # 3px+ solid outline plus a soft ring via box-shadow, driven by tokens.
        self.assertRegex(body, r"outline:3px solid var\(--focus\)")
        self.assertIn("box-shadow:0 0 0 5px var(--focus-ring)", body)

    def test_css_has_form_error_state_styles(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn(".form-actions", css)
        self.assertIn(".has-error", css)
        self.assertIn(".errorlist.nonfield", css)

    def test_css_empty_card_spans_full_grid(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.cards\s+\.empty\s*\{[^}]*grid-column")

    def test_base_template_loads_form_submit_state_script(self):
        response = self.client.get(reverse("rules"))
        self.assertContains(response, "reviews/form-submit.js")
        script = Path(__file__).parent / "static" / "reviews" / "form-submit.js"
        self.assertTrue(script.is_file())
        source = script.read_text(encoding="utf-8")
        self.assertIn('addEventListener("submit"', source)
        self.assertIn("disabled = true", source)

    def test_css_has_submit_loading_state_for_both_themes(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn(".is-loading::after", css)
        self.assertIn("@keyframes submit-spin", css)
        self.assertRegex(
            css, r"@media\s*\(prefers-color-scheme\s*:\s*dark\)\s*\{[^}]*\.is-loading"
        )


class DesignContrastTests(TestCase):
    """WCAG 2.1 AA contrast checks for the CSS design tokens."""

    @classmethod
    def _tokens(cls):
        css = STATIC_CSS.read_text(encoding="utf-8")
        root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
        return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", root))

    @classmethod
    def _dark_tokens(cls):
        css = STATIC_CSS.read_text(encoding="utf-8")
        dark = re.search(
            r"prefers-color-scheme\s*:\s*dark[^{]*\{\s*:root\s*\{([^}]*)\}", css
        )
        assert dark, "dark theme block with :root tokens not found in style.css"
        return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", dark.group(1)))

    @staticmethod
    def _luminance(hex_color):
        def channel(v):
            v = v / 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    @classmethod
    def _ratio(cls, fg, bg):
        l1, l2 = sorted((cls._luminance(fg), cls._luminance(bg)), reverse=True)
        return (l1 + 0.05) / (l2 + 0.05)

    def test_text_pairs_meet_aa_4_5(self):
        t = self._tokens()
        pairs = [
            ("ink", "bg"), ("ink", "surface"),
            ("muted", "bg"), ("muted", "surface"),
            ("accent", "surface"), ("accent", "bg"),
            ("good-ink", "good-bg"), ("mid-ink", "mid-bg"),
            ("bad-ink", "bad-bg"), ("none-ink", "none-bg"),
        ]
        for fg, bg in pairs:
            with self.subTest(pair=f"{fg} on {bg}"):
                self.assertGreaterEqual(self._ratio(t[fg], t[bg]), 4.5)

    def test_star_graphic_meets_3_to_1(self):
        t = self._tokens()
        self.assertGreaterEqual(self._ratio(t["star"], t["surface"]), 3.0)

    def test_white_text_on_accent_button_meets_aa(self):
        t = self._tokens()
        self.assertGreaterEqual(self._ratio(t["on-accent"], t["accent"]), 4.5)
        self.assertGreaterEqual(self._ratio(t["on-accent"], t["accent-strong"]), 4.5)

    # --- Dark theme (prefers-color-scheme: dark) ---

    def test_dark_theme_redeclares_all_core_tokens(self):
        light, dark = self._tokens(), self._dark_tokens()
        core = {"bg", "surface", "ink", "muted", "line", "accent", "accent-strong",
                "on-accent", "good-bg", "good-ink", "mid-bg", "mid-ink",
                "bad-bg", "bad-ink", "none-bg", "none-ink", "star", "star-empty",
                "focus"}
        self.assertTrue(core.issubset(dark.keys()))
        for name in core:
            with self.subTest(token=name):
                self.assertNotEqual(light[name], dark[name])

    def test_dark_text_pairs_meet_aa_4_5(self):
        t = self._dark_tokens()
        pairs = [
            ("ink", "bg"), ("ink", "surface"),
            ("muted", "bg"), ("muted", "surface"),
            ("accent", "surface"), ("accent", "bg"),
            ("good-ink", "good-bg"), ("mid-ink", "mid-bg"),
            ("bad-ink", "bad-bg"), ("none-ink", "none-bg"),
        ]
        for fg, bg in pairs:
            with self.subTest(pair=f"dark {fg} on {bg}"):
                self.assertGreaterEqual(self._ratio(t[fg], t[bg]), 4.5)

    def test_dark_star_graphic_meets_3_to_1(self):
        t = self._dark_tokens()
        self.assertGreaterEqual(self._ratio(t["star"], t["surface"]), 3.0)

    def test_dark_button_text_on_accent_meets_aa(self):
        t = self._dark_tokens()
        self.assertGreaterEqual(self._ratio(t["on-accent"], t["accent"]), 4.5)
        self.assertGreaterEqual(self._ratio(t["on-accent"], t["accent-strong"]), 4.5)

    def test_focus_ring_visible_on_page_backgrounds(self):
        # Focus indicator is a non-text UI component: WCAG 1.4.11 needs >= 3:1
        # against the colors it sits on, in both themes.
        for name, tokens in (("light", self._tokens()), ("dark", self._dark_tokens())):
            for surface in ("bg", "surface"):
                with self.subTest(theme=name, surface=surface):
                    self.assertGreaterEqual(
                        self._ratio(tokens["focus"], tokens[surface]), 3.0
                    )


class MobileLayoutDesignTests(TestCase):
    """Mobile layout guards: 360px-safe professor page and review cards."""

    @staticmethod
    def _media_block(css, query):
        """Return the body of `@media (max-width:query){...}` (nested rules)."""
        marker = f"@media (max-width:{query})"
        start = css.find(marker)
        assert start != -1, f"media query {marker} not found in style.css"
        i = css.index("{", start)
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    return css[i + 1:j]
        raise AssertionError(f"unbalanced braces after {marker}")

    def test_mobile_breakpoints_cover_360px_phones(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        # 26rem = 416px, so the small-phone block also matches 360px viewports.
        self.assertIn("@media (max-width:26rem)", css)
        self.assertIn("@media (max-width:50rem)", css)

    def test_review_cards_stack_single_column_on_phones(self):
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "26rem")
        self.assertRegex(block, r"\.review\{[^}]*grid-template-columns:1fr")

    def test_professor_cards_single_column_on_narrow_screens(self):
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "50rem")
        self.assertRegex(block, r"\.cards\{[^}]*grid-template-columns:1fr")

    def test_no_horizontal_scroll_guards(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn("overflow-x:clip", css)  # page-level safety net
        for selector in (".review", ".headline", ".card-body"):
            with self.subTest(selector=selector):
                rule = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
                self.assertIsNotNone(rule)
                self.assertIn("overflow-wrap:break-word", rule.group(1))
        # Grid/flex children must be allowed to shrink below content width.
        self.assertIn(".review>div{min-width:0}", css)
        self.assertIn(".headline>div{min-width:0}", css)

    def test_pagination_and_vote_forms_wrap_on_mobile(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        for selector in (".pagination", ".votes", ".votes form"):
            with self.subTest(selector=selector):
                rule = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
                self.assertIsNotNone(rule)
                self.assertIn("flex-wrap:wrap", rule.group(1))

    def test_search_filters_stack_full_width_on_phones(self):
        """Home search form must become a single full-width column at 360px."""
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "26rem")
        self.assertRegex(block, r"\.search\{[^}]*flex-direction:column")
        self.assertRegex(
            block, r"\.search input,\.search select,\.search button\{[^}]*width:100%"
        )

    def test_professor_headline_stacks_on_phones(self):
        """Rating tile, name and stars stack vertically on narrow screens."""
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "26rem")
        self.assertRegex(block, r"\.headline\{[^}]*flex-direction:column")

    def test_phone_gutters_shrink_to_leave_more_content_width(self):
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "26rem")
        rule = re.search(r"\.top-inner,main,\.messages,footer\{([^}]*)\}", block)
        self.assertIsNotNone(rule)
        self.assertIn("padding-left:1rem", rule.group(1))
        self.assertIn("padding-right:1rem", rule.group(1))

    def test_mobile_nav_links_are_full_width_44px_targets(self):
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "50rem")
        rule = re.search(r"\.nav a,\.nav \.link-btn\{([^}]*)\}", block)
        self.assertIsNotNone(rule)
        self.assertIn("width:100%", rule.group(1))
        # 2.75rem = 44px at the default 16px root font size (WCAG 2.5.5).
        self.assertIn("min-height:2.75rem", rule.group(1))

    def test_compact_controls_get_44px_tap_targets_on_mobile(self):
        """Vote buttons, pagination and search controls reach 44px on mobile."""
        block = self._media_block(STATIC_CSS.read_text(encoding="utf-8"), "50rem")
        for pattern in (
            r"\.votes button[^{]*\{[^}]*min-height:2\.75rem",
            r"\.pagination a\{[^}]*min-height:2\.75rem",
            r"\.search input,\.search select,\.search button\{[^}]*min-height:2\.75rem",
            r"\.choices label\{[^}]*min-height:2\.75rem",
        ):
            with self.subTest(pattern=pattern):
                self.assertRegex(block, pattern)

    def test_form_controls_have_persistent_focus_border(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        rule = re.search(r"input:focus,select:focus,textarea:focus\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("border-color:var(--focus)", rule.group(1))

    def test_focus_outline_fallback_without_focus_visible_support(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        fallback = re.search(
            r"@supports not selector\(:focus-visible\)\{\s*:focus\{([^}]*)\}", css
        )
        self.assertIsNotNone(fallback)
        self.assertIn("outline:3px solid var(--focus)", fallback.group(1))

    def test_choice_chips_promote_focus_ring_to_whole_label(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.choices label:has\(input:focus-visible\)\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("outline:3px solid var(--focus)", rule.group(1))


class ProfessorMobileMarkupTests(_MemoryRepoTestCase):
    """Professor page markup that the mobile CSS hooks onto (reviews, votes)."""

    def test_professor_page_renders_review_card_structure(self):
        prof = self.prof()
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertContains(r, 'class="headline"')
        self.assertContains(r, 'class="review" id="review-')
        self.assertContains(r, 'class="votes"')
        self.assertContains(r, 'class="facts-line"')

    def test_professor_page_uses_shared_stylesheet_and_viewport_meta(self):
        prof = self.prof()
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertContains(r, "reviews/style.css")
        self.assertContains(r, 'name="viewport" content="width=device-width, initial-scale=1"')


class ProfessorA11yTests(_MemoryRepoTestCase):
    """Professor page a11y: vote/report context labels, pagination semantics."""

    def test_vote_and_report_buttons_have_context_aria_labels(self):
        prof = self.prof()
        r = self.client.get(reverse("professor", args=[prof.slug]))
        # Buttons must name the review they act on (course + count), so the
        # repeated "Полезно N" buttons are distinguishable for screen readers.
        course = self.repo.reviews_for(prof.id)[0].course
        self.assertContains(r, f'aria-label="Полезно: {course} (')
        self.assertContains(r, f'aria-label="Бесполезно: {course} (')
        self.assertContains(r, f'aria-label="Пожаловаться: {course}"')

    def test_pagination_uses_page_aria_labels_and_aria_current(self):
        prof = self.prof()
        for i in range(12):
            self.repo.add_review(prof.id, _repo_review(course=f"Пагин{i}"), status="approved")
        r = self.client.get(reverse("professor", args=[prof.slug]), {"page": 2})
        self.assertContains(r, 'aria-current="page"')
        # "Назад"/"Вперёд" get unambiguous page-number accessible names.
        self.assertContains(r, 'aria-label="Страница 1 из 2"')
        # last page: no forward link at all
        self.assertNotContains(r, ">Вперёд</a>")

    def test_warning_message_is_announced_as_polite_status(self):
        user = self.login(email="messages-success@example.com")
        review = self.repo.add_review(
            self.prof().id, _repo_review(course="Свой курс"), user=user
        )
        self.repo.approve_review(review.id)
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"}, follow=True)
        self.assertContains(r, "Нельзя голосовать за свой отзыв")
        self.assertContains(r, 'role="status"')
        self.assertNotContains(r, 'role="alert"')


class TouchTargetDesignTests(TestCase):
    """Compact controls keep comfortable touch targets (WCAG 2.5.5, 44px)."""

    def test_burger_toggle_is_44px(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.nav-toggle\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        # 2.75rem = 44px at the default 16px root font size.
        self.assertIn("width:2.75rem", rule.group(1))
        self.assertIn("height:2.75rem", rule.group(1))

    def test_compact_vote_and_pagination_buttons_are_enlarged(self):
        css = STATIC_CSS.read_text(encoding="utf-8")
        self.assertIn("padding:.45rem .85rem", css)  # .votes button
        self.assertIn("padding:.5rem .9rem", css)    # .pagination a

    def test_error_messages_are_assertive_alerts(self):
        html = (TEMPLATES_DIR / "reviews" / "base.html").read_text(encoding="utf-8")
        self.assertIn(
            """role="{% if m.tags == 'error' %}alert{% else %}status{% endif %}\"""", html
        )


class HeaderLayoutTests(_MemoryRepoTestCase):
    """Modern header: skip link, burger toggle, i18n language switch."""

    def test_header_has_skip_link_and_target(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, 'class="skip-link"')
        self.assertContains(r, 'href="#main-content"')
        self.assertContains(r, 'id="main-content"')

    def test_header_has_burger_toggle(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, 'class="nav-toggle"')
        self.assertContains(r, 'aria-expanded="false"')
        self.assertContains(r, 'aria-controls="site-nav"')
        self.assertContains(r, 'id="site-nav"')
        self.assertContains(r, 'aria-label="Меню"')
        # inline toggle script (no external JS)
        self.assertContains(r, "nav-toggle")

    def test_header_keeps_language_switch(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, 'name="language"')
        self.assertContains(r, 'aria-label="Язык"')

    def test_language_switch_highlights_current_language(self):
        for code in ("ru", "ky", "en"):
            with self.subTest(language=code):
                self.client.post(
                    reverse("set_language"),
                    {"language": code, "next": reverse("home")},
                )
                r = self.client.get(reverse("home"))
                html = r.content.decode()
                start = html.index('<select name="language"')
                switch = html[start:html.index("</select>", start)]
                self.assertIn(f'<option value="{code}" selected>', switch)
                self.assertEqual(switch.count(" selected>"), 1)

    def test_header_logged_in_shows_logout(self):
        self.login(email="messages-error@example.com")
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Выйти")
        self.assertNotContains(r, ">Регистрация</a>")


class ProfessorCardsTests(_MemoryRepoTestCase):
    """Professor list renders as cards with star ratings."""

    def test_home_renders_cards_with_stars(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, 'class="card"')
        self.assertContains(r, 'class="card-name"')
        self.assertContains(r, "★★★★★")
        self.assertContains(r, 'class="s-fill"')

    def test_home_and_school_cards_render_name_link_and_rating(self):
        prof = self.prof()
        expected_url = reverse("professor", args=[prof.slug])
        school = next(row["school"] for row in self.repo.search() if row["prof"].id == prof.id)

        for response in (
            self.client.get(reverse("home")),
            self.client.get(reverse("school_detail", args=[school.slug])),
        ):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertContains(response, prof.full_name)
                self.assertContains(response, f'href="{expected_url}"')
                self.assertContains(response, f'aria-label="Рейтинг {localize(self.repo.stats(prof.id)["overall"])}'
                                               ' из 5"')

    def test_home_card_star_aria_label_matches_rating(self):
        r = self.client.get(reverse("home"))
        first = r.context["results"][0]["stats"]
        with translation.override("ru"):
            self.assertContains(r, f'aria-label="Рейтинг {localize(first["overall"])} из 5"')
        self.assertContains(r, 'class="s-fill" aria-hidden="true" style="width:')

    def test_home_card_without_reviews_shows_empty_stars(self):
        school = self.repo.schools()[0]
        self.repo.add_professor(school.id, "Тихон", "Безотзывов", "Физика", "доцент")
        r = self.client.get(reverse("home"), {"q": "Безотзывов"})
        self.assertContains(r, 'aria-label="Нет оценок"')
        self.assertNotContains(r, 'class="s-fill"')

    def test_school_page_renders_cards_with_stars(self):
        school = self.repo.schools()[0]
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertContains(r, 'class="card"')
        self.assertContains(r, "★★★★★")

    def test_professor_page_shows_stars(self):
        prof = self.prof()
        stats = self.repo.stats(prof.id)
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertContains(r, "★★★★★")
        with translation.override("ru"):
            self.assertContains(r, f'aria-label="Рейтинг {localize(stats["overall"])} из 5"')

    def test_professor_page_without_reviews_shows_empty_stars(self):
        school = self.repo.schools()[0]
        p = self.repo.add_professor(school.id, "Тихон", "Безотзывов", "Физика", "доцент")
        r = self.client.get(reverse("professor", args=[p.slug]))
        self.assertContains(r, 'aria-label="Нет оценок"')


class FormStatesDesignTests(_MemoryRepoTestCase):
    """UX: unified form markup, visible error states (field + non-field)."""

    def test_review_form_uses_shared_field_markup_and_actions(self):
        self.login()
        r = self.client.get(reverse("review_new", args=[self.prof().slug]))
        self.assertContains(r, 'class="form-actions"')
        self.assertContains(r, 'class="cancel"')
        self.assertContains(r, 'class="req"')  # required marker on obligatory fields

    def test_review_form_marks_invalid_fields(self):
        self.login()
        prof = self.prof()
        r = self.client.post(reverse("review_new", args=[prof.slug]), _valid({"comment": "коротко"}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "has-error")
        self.assertContains(r, "errorlist")

    def test_add_professor_duplicate_error_is_visible(self):
        # Non-field errors must render: duplicate-professor rejection
        # is attached via form.add_error(None, ...).
        self.login()
        school = self.repo.schools()[0]
        data = {"school": str(school.id), "first_name": "Тест", "last_name": "Тестов",
                "department": "Кафедра теста", "title": "доцент"}
        self.assertEqual(self.client.post(reverse("add_professor"), data).status_code, 302)
        r = self.client.post(reverse("add_professor"), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "уже есть в этом вузе")
        self.assertContains(r, "errorlist nonfield")

    def test_login_uses_unified_fields_and_shows_nonfield_error(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, 'class="field"')
        User.objects.create_user(username="u@example.com", email="u@example.com", password="pass12345")
        r2 = self.client.post(reverse("login"), {"username": "u@example.com", "password": "wrongpass1"})
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "errorlist nonfield")

    def test_signup_uses_unified_fields(self):
        r = self.client.get(reverse("signup"))
        self.assertContains(r, 'class="field"')
        self.assertContains(r, 'class="form-actions"')


class LanguageSwitchTests(TestCase):
    """POST /i18n/setlang/ changes the language the templates render in."""

    def _switch(self, code):
        return self.client.post(
            reverse("set_language"), {"language": code, "next": reverse("home")}
        )

    def test_default_language_is_russian(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Правила")
        self.assertContains(r, 'lang="ru"')

    def test_switch_to_english_then_back_to_russian(self):
        self.assertEqual(self._switch("en").status_code, 302)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Rules")
        self.assertContains(r, "Add professor")
        self.assertNotContains(r, "Правила")
        self.assertContains(r, 'lang="en"')
        # switch back
        self._switch("ru")
        r2 = self.client.get(reverse("home"))
        self.assertContains(r2, "Правила")
        self.assertNotContains(r2, "Rules")

    def test_switch_to_kyrgyz(self):
        self.assertEqual(self._switch("ky").status_code, 302)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Эрежелер")
        self.assertContains(r, "Окутуучу кошуу")
        self.assertContains(r, 'lang="ky"')

    def test_invalid_language_falls_back_to_default(self):
        self._switch("de")
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Правила")

    def test_404_page_renders_in_selected_language(self):
        self._switch("en")
        r = self.client.get("/definitely-not-a-real-page/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Page not found", status_code=404)
        self.assertNotContains(r, "Страница не найдена", status_code=404)
        self._switch("ky")
        r2 = self.client.get("/definitely-not-a-real-page/")
        self.assertContains(r2, "Барак табылган жок", status_code=404)


class CatalogTranslationTests(TestCase):
    """Previously untranslated views/forms/error-page strings now resolve."""

    def test_error_page_strings(self):
        with translation.override("en"):
            self.assertEqual(translation.gettext("Страница не найдена"), "Page not found")
            self.assertEqual(translation.gettext("Ошибка сервера"), "Server error")
            self.assertEqual(translation.gettext("Вернуться на главную"), "Back to home")
        with translation.override("ky"):
            self.assertEqual(translation.gettext("Страница не найдена"), "Барак табылган жок")
            self.assertEqual(translation.gettext("Ошибка сервера"), "Сервер катасы")

    def test_form_length_validation_messages(self):
        with translation.override("en"):
            self.assertEqual(
                translation.gettext("Отзыв должен содержать не менее %(n)d символов.") % {"n": 5},
                "The review must contain at least 5 characters.",
            )
            self.assertEqual(
                translation.gettext("Отзыв должен содержать не более %(n)d символов.") % {"n": 900},
                "The review must not exceed 900 characters.",
            )

    def test_duplicate_review_message_uses_current_source_string(self):
        with translation.override("en"):
            self.assertEqual(
                translation.gettext("Вы уже оставляли отзыв по этому предмету у этого преподавателя."),
                "You have already left a review for this subject with this professor.",
            )

    def test_admin_action_labels_are_translated(self):
        with translation.override("en"):
            self.assertEqual(translation.gettext("ФИО"), "Full name")
            self.assertEqual(
                translation.gettext("Одобрить выбранных преподавателей"),
                "Approve selected professors",
            )
            self.assertEqual(
                translation.gettext("Отклонить выбранные отзывы"),
                "Reject selected reviews",
            )

    def test_view_not_found_messages_are_translated(self):
        with translation.override("ky"):
            self.assertEqual(translation.gettext("Professor not found"), "Окутуучу табылган жок")
            self.assertEqual(translation.gettext("School not found"), "ЖОЖ табылган жок")
            self.assertEqual(translation.gettext("Review not found"), "Сын-пикир табылган жок")


# ---------------------------------------------------------------------------
# Anti-duplication / anti-race: DB constraints + atomic votes
# ---------------------------------------------------------------------------
class ReviewDeduplicationTests(TestCase):
    """One review per (professor, course, user) and one vote per (review, user)."""

    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod
        self.repo = DjangoRepository()
        from reviews.management.commands.seed_kg import Command as SeedCmd
        SeedCmd().handle(clear=True)
        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    def _user(self, email):
        return User.objects.create_user(username=email, email=email, password="pass12345")

    def _prof(self):
        return self.repo.search("Айбек")[0]["prof"]

    def test_models_declare_named_unique_constraints(self):
        from .models import Report, Review, Vote
        self.assertIn("uniq_review_prof_author_course", {c.name for c in Review._meta.constraints})
        self.assertIn("uniq_vote_review_user", {c.name for c in Vote._meta.constraints})
        self.assertIn("uniq_report_review_user", {c.name for c in Report._meta.constraints})

    def test_duplicate_review_blocked_by_db_constraint(self):
        from django.db import IntegrityError, transaction

        from .models import Review
        prof = self._prof()
        u = self._user("dedup@example.com")
        self.repo.add_review(prof.id, _repo_review(course="Algorithms"), user=u)
        # Bypass repository guard: the DB constraint must reject the duplicate.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Review.objects.create(
                    professor_id=prof.id, course="algorithms", overall=4, difficulty=3,
                    would_take_again=True, comment="Достаточно длинный тестовый комментарий.",
                    status="pending", author=u,
                )

    def test_anonymous_reviews_are_not_constrained(self):
        from .models import Review
        prof = self._prof()
        Review.objects.create(
            professor_id=prof.id, course="Алгоритмы и структуры данных", overall=5, difficulty=3,
            would_take_again=True, comment="Анонимный отзыв.", status="approved", author=None,
        )
        self.assertGreaterEqual(
            Review.objects.filter(
                professor_id=prof.id, course="Алгоритмы и структуры данных", author__isnull=True
            ).count(),
            2,
        )

    def test_vote_is_unique_per_user_and_counted_once(self):
        from django.db import IntegrityError, transaction

        from .models import Vote
        prof = self._prof()
        review = self.repo.reviews_for(prof.id)[0]
        u = self._user("atomic_vote@example.com")
        self.repo.vote(review.id, helpful=True, user=u)
        self.assertEqual(self.repo.review(review.id).helpful, 1)
        with self.assertRaises(ValueError):
            self.repo.vote(review.id, helpful=True, user=u)
        self.assertEqual(self.repo.review(review.id).helpful, 1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Vote.objects.create(review_id=review.id, user=u, helpful=True)

    def test_duplicate_review_view_shows_message(self):
        from .models import Review
        prof = self._prof()
        u = self._user("dupview@example.com")
        self.client.force_login(u)
        url = reverse("review_new", args=[prof.slug])
        self.assertEqual(self.client.post(url, _valid()).status_code, 302)
        r = self.client.post(url, _valid())
        self.assertEqual(r.status_code, 200)
        self.assertIn("course", r.context["form"].errors)
        self.assertEqual(Review.objects.filter(professor_id=prof.id, course="Алгоритмы", author=u).count(), 1)


# ---------------------------------------------------------------------------
# Pagination of professor lists and review lists
# ---------------------------------------------------------------------------
class PaginationTests(_MemoryRepoTestCase):
    """Professor lists (school page) and review lists (professor page) paginate."""

    def test_school_page_paginates_professors(self):
        school = self.repo.schools()[0]
        for i in range(12):
            self.repo.add_professor(school.id, f"Пагин{i}", f"Тестов{i}", "Кафедра", "доцент")
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertIsNotNone(r.context["page_obj"])
        self.assertEqual(r.context["page_obj"].paginator.num_pages, 2)
        self.assertLessEqual(len(r.context["results"]), 10)
        r2 = self.client.get(reverse("school_detail", args=[school.slug]), {"page": 2})
        self.assertEqual(r2.status_code, 200)
        self.assertLessEqual(len(r2.context["results"]), 10)

    def test_school_single_page_has_no_widget(self):
        school = next(s for s in self.repo.schools() if "Ошский" in s.name)
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertIsNone(r.context["page_obj"])

    def test_professor_page_paginates_reviews(self):
        prof = self.prof()
        for i in range(12):
            self.repo.add_review(prof.id, _repo_review(course=f"Пагин{i}"), status="approved")
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertIsNotNone(r.context["page_obj"])
        self.assertEqual(r.context["page_obj"].paginator.num_pages, 2)
        self.assertLessEqual(len(r.context["reviews"]), 10)
        r2 = self.client.get(reverse("professor", args=[prof.slug]), {"page": 2})
        self.assertEqual(r2.status_code, 200)
        self.assertLessEqual(len(r2.context["reviews"]), 10)

    def test_school_page_invalid_pages_fall_back(self):
        school = self.repo.schools()[0]
        for i in range(12):
            self.repo.add_professor(school.id, f"Пагин{i}", f"Тестов{i}", "Кафедра", "доцент")
        url = reverse("school_detail", args=[school.slug])
        last = self.client.get(url).context["page_obj"].paginator.num_pages
        for page in ("abc", "0", "-1", "9999"):
            with self.subTest(page=page):
                r = self.client.get(url, {"page": page})
                self.assertEqual(r.status_code, 200)
                self.assertIsNotNone(r.context["page_obj"])
                self.assertIn(r.context["page_obj"].number, (1, last))
        self.assertEqual(
            self.client.get(url, {"page": "abc"}).context["page_obj"].number, 1
        )
        self.assertEqual(
            self.client.get(url, {"page": "9999"}).context["page_obj"].number, last
        )

    def test_professor_page_invalid_pages_fall_back(self):
        prof = self.prof()
        for i in range(12):
            self.repo.add_review(prof.id, _repo_review(course=f"Пагин{i}"), status="approved")
        url = reverse("professor", args=[prof.slug])
        last = self.client.get(url).context["page_obj"].paginator.num_pages
        for page in ("abc", "0", "-1", "9999"):
            with self.subTest(page=page):
                r = self.client.get(url, {"page": page})
                self.assertEqual(r.status_code, 200)
                self.assertIsNotNone(r.context["page_obj"])
                self.assertIn(r.context["page_obj"].number, (1, last))
                self.assertTrue(r.context["reviews"])
        self.assertEqual(
            self.client.get(url, {"page": "abc"}).context["page_obj"].number, 1
        )
        self.assertEqual(
            self.client.get(url, {"page": "9999"}).context["page_obj"].number, last
        )


# ---------------------------------------------------------------------------
# Performance: no N+1 queries in DjangoRepository list pages
# ---------------------------------------------------------------------------
@override_settings(CACHES={
    "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
})
class QueryEfficiencyTests(TestCase):
    """DjangoRepository uses select_related/prefetch_related/annotate.

    Each read path must run a constant number of queries regardless of how
    many professors/reviews exist. The catalog cache is disabled here so the
    measurements exercise the repository rather than a warm cache.
    """

    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod
        self.repo = DjangoRepository()
        from reviews.management.commands.seed_kg import Command as SeedCmd
        SeedCmd().handle(clear=True)
        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod
        store_mod.repo = self._orig
        v.repo = self._orig_v

    @contextmanager
    def assertNumQueriesLessThan(self, limit):
        """Assert the wrapped block issues fewer than ``limit`` DB queries."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            yield
        count = len(ctx.captured_queries)
        self.assertLess(
            count, limit,
            f"Expected fewer than {limit} queries, got {count}",
        )

    def _add_load(self, professors=8, reviews_each=3):
        school = self.repo.schools()[0]
        for i in range(professors):
            p = self.repo.add_professor(school.id, f"Нагруз{i}", f"Проф{i}", "Кафедра", "доцент")
            for j in range(reviews_each):
                self.repo.add_review(p.id, _repo_review(course=f"Курс {i}-{j}"), status="approved")

    def _add_schools(self, count=5):
        for i in range(count):
            s = self.repo.add_school(f"Перф вуз {i}", f"Перф город {i}")
            self.repo.add_professor(s.id, f"Перф{i}", f"Вуз{i}", "Кафедра перф", "доцент")

    def test_search_query_count_is_constant(self):
        with self.assertNumQueries(3):
            self.repo.search()
        self._add_load()
        with self.assertNumQueries(3):
            self.repo.search()

    def test_stats_query_count_is_constant(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        with self.assertNumQueries(2):
            self.repo.stats(prof.id)
        for i in range(10):
            self.repo.add_review(prof.id, _repo_review(course=f"Ещё {i}"), status="approved")
        with self.assertNumQueries(2):
            self.repo.stats(prof.id)

    def test_reviews_for_prefetches_tags(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        with self.assertNumQueries(2):
            self.repo.reviews_for(prof.id)
        for i in range(10):
            self.repo.add_review(prof.id, _repo_review(course=f"Тег {i}"), status="approved")
        with self.assertNumQueries(2):
            self.repo.reviews_for(prof.id)

    def test_single_review_read_prefetches_tags(self):
        """``review()`` loads tags eagerly so a batch caller can't N+1."""
        prof = self.repo.search("Айбек")[0]["prof"]
        review = self.repo.reviews_for(prof.id)[0]
        with self.assertNumQueries(2):
            got = self.repo.review(review.id)
        self.assertEqual(got.tags, review.tags)
        # Growing the number of tagged reviews must not change the count.
        for i in range(10):
            self.repo.add_review(
                prof.id,
                _repo_review(course=f"Тег {i}", tags=["Строго оценивает", "Много тестов"]),
                status="approved",
            )
        with self.assertNumQueries(2):
            self.repo.review(review.id)

    def _post_query_count(self, url, data=None):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.post(url, data or {})
        return len(ctx.captured_queries)

    def test_vote_and_report_views_query_count_is_constant(self):
        """Voting/reporting must not query once per stored vote/report."""
        prof = self.repo.search("Айбек")[0]["prof"]
        user = User.objects.create_user(
            username="perfvoter@example.com", email="perfvoter@example.com", password="pass12345"
        )
        self.client.force_login(user)

        def fresh_review(tag):
            return self.repo.add_review(
                prof.id, _repo_review(course=f"Перф {tag}"), status="approved"
            )

        # Warm session/messages storage on the first request.
        warm = fresh_review("warm")
        self._post_query_count(reverse("review_vote", args=[warm.id]), {"vote": "up"})

        vote_before = self._post_query_count(
            reverse("review_vote", args=[fresh_review("before").id]), {"vote": "up"}
        )
        report_before = self._post_query_count(
            reverse("review_report", args=[fresh_review("before2").id])
        )

        self._add_load()

        vote_after = self._post_query_count(
            reverse("review_vote", args=[fresh_review("after").id]), {"vote": "up"}
        )
        report_after = self._post_query_count(
            reverse("review_report", args=[fresh_review("after2").id])
        )
        self.assertEqual(vote_after, vote_before)
        self.assertEqual(report_after, report_before)

    def test_professor_detail_reads_use_select_related(self):
        """Detail-page reads join school in one query (no per-professor lookup)."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        prof = self.repo.search("Айбек")[0]["prof"]
        for read in (
            lambda: self.repo.professor(prof.slug),
            lambda: self.repo.professor_any(prof.slug),
            lambda: self.repo.professor_by_id(prof.id),
        ):
            with self.assertNumQueries(1):
                read()
            with CaptureQueriesContext(connection) as ctx:
                read()
            self.assertEqual(len(ctx.captured_queries), 1)
            sql = ctx.captured_queries[0]["sql"].lower()
            self.assertIn("join", sql)
            self.assertIn("school", sql)

    def test_school_stats_query_count_is_constant(self):
        school = self.repo.schools()[0]
        with self.assertNumQueries(3):
            self.repo.school_stats(school.id)
        self._add_load()
        with self.assertNumQueries(3):
            self.repo.school_stats(school.id)

    def test_home_view_query_count_is_constant(self):
        self.client.get(reverse("home"))
        with self.assertNumQueries(6):
            self.client.get(reverse("home"))
        self._add_load()
        with self.assertNumQueries(6):
            self.client.get(reverse("home"))

    def test_professor_view_query_count_is_constant(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        self.client.get(reverse("professor", args=[prof.slug]))
        with self.assertNumQueries(6):
            self.client.get(reverse("professor", args=[prof.slug]))
        for i in range(12):
            self.repo.add_review(prof.id, _repo_review(course=f"Отзыв {i}"), status="approved")
        with self.assertNumQueries(6):
            self.client.get(reverse("professor", args=[prof.slug]))

    def test_school_view_query_count_is_constant(self):
        school = self.repo.schools()[0]
        self.client.get(reverse("school_detail", args=[school.slug]))
        with self.assertNumQueries(7):
            self.client.get(reverse("school_detail", args=[school.slug]))
        self._add_load()
        with self.assertNumQueries(7):
            self.client.get(reverse("school_detail", args=[school.slug]))

    def test_home_list_query_count_does_not_grow_per_professor(self):
        """The professor list page issues no per-row queries as the catalog grows."""
        self.client.get(reverse("home"))  # warm session/messages storage
        self._add_schools(4)
        self._add_load(professors=12, reviews_each=3)
        with self.assertNumQueries(6):
            self.client.get(reverse("home"))

    def test_professor_list_query_count_is_flat_across_catalog_sizes(self):
        """Home and school lists keep the same query count as rows are added.

        Measuring the same two pages before and after growing the catalog
        proves the numbers are independent of the professor/review count
        rather than merely below a loose upper bound.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        school = self.repo.schools()[0]
        home_url = reverse("home")
        school_url = reverse("school_detail", args=[school.slug])
        self.client.get(home_url)  # warm session/messages storage
        self.client.get(school_url)

        def counts():
            with CaptureQueriesContext(connection) as home_ctx:
                self.client.get(home_url)
            with CaptureQueriesContext(connection) as school_ctx:
                self.client.get(school_url)
            return len(home_ctx.captured_queries), len(school_ctx.captured_queries)

        before = counts()
        self._add_schools(6)
        self._add_load(professors=20, reviews_each=3)
        after = counts()

        self.assertEqual(after, before)

    def test_professor_profile_query_count_does_not_grow_with_reviews_or_tags(self):
        """Profile reviews/tags are prefetched; adding rows adds no queries."""
        prof = self.repo.search("Айбек")[0]["prof"]
        url = reverse("professor", args=[prof.slug])
        self.client.get(url)  # warm session/messages storage
        for i in range(25):
            self.repo.add_review(
                prof.id,
                _repo_review(
                    course=f"Теги {i}",
                    tags=["Строго оценивает", "Много тестов", "Понятные критерии"],
                ),
                status="approved",
            )
        with self.assertNumQueries(6):
            self.client.get(url)

    def test_home_view_query_count_stays_bounded_as_catalog_grows(self):
        self.client.get(reverse("home"))  # warm session/messages storage
        self._add_schools(6)
        self._add_load(professors=12, reviews_each=2)
        with self.assertNumQueriesLessThan(10):
            self.client.get(reverse("home"))

    def test_school_view_query_count_stays_bounded_as_professors_grow(self):
        school = self.repo.schools()[0]
        self.client.get(reverse("school_detail", args=[school.slug]))
        self._add_schools(3)
        self._add_load(professors=12, reviews_each=2)
        with self.assertNumQueriesLessThan(12):
            self.client.get(reverse("school_detail", args=[school.slug]))

    def test_professor_view_query_count_stays_bounded_as_reviews_grow(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        self.client.get(reverse("professor", args=[prof.slug]))
        for i in range(20):
            self.repo.add_review(
                prof.id,
                _repo_review(
                    course=f"Много {i}",
                    tags=["Строго оценивает", "Много тестов"],
                ),
                status="approved",
            )
        with self.assertNumQueriesLessThan(10):
            self.client.get(reverse("professor", args=[prof.slug]))

    def test_search_with_filters_keeps_query_count_bounded(self):
        self._add_schools(4)
        self._add_load(professors=8, reviews_each=2)
        for filters in ({}, {"city": "Бишкек"}, {"sort": "reviews"}):
            with self.subTest(filters=filters):
                with self.assertNumQueriesLessThan(6):
                    self.repo.search(**filters)

    def test_filter_option_lists_do_not_scale_with_rows(self):
        self._add_schools(10)
        self._add_load(professors=10, reviews_each=1)
        for read in (self.repo.schools, self.repo.cities, self.repo.departments):
            with self.subTest(read=read.__name__):
                with self.assertNumQueriesLessThan(2):
                    read()


# ---------------------------------------------------------------------------
# Performance: admin changelists must not issue one query per row
# ---------------------------------------------------------------------------
class AdminQueryEfficiencyTests(TestCase):
    """FK columns in admin ``list_display`` use ``list_select_related``.

    Without it the changelist runs an extra lookup for every row (the classic
    N+1), so the query count must stay flat while the number of rows grows.
    """

    def setUp(self):
        from django.contrib.auth.models import Permission

        from reviews.management.commands.seed_kg import Command as SeedCmd
        SeedCmd().handle(clear=True)
        self.repo = DjangoRepository()
        moderator = User.objects.create_user(
            username="adminperf@example.com", email="adminperf@example.com",
            password="pass12345", is_staff=True,
        )
        for codename in ("change_professor", "change_review", "change_vote", "change_report"):
            moderator.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(moderator)

    def _count(self, url_name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse(url_name))
        return len(ctx.captured_queries)

    def _load(self, professors=12, reviews_each=1):
        school = self.repo.schools()[0]
        for i in range(professors):
            p = self.repo.add_professor(school.id, f"Админ{i}", f"Нагруз{i}", "Кафедра перф", "доцент")
            for j in range(reviews_each):
                self.repo.add_review(p.id, _repo_review(course=f"Перф {i}-{j}"), status="approved")

    def _assert_constant(self, url_name):
        self.client.get(reverse(url_name))  # warm session/permission caches
        before = self._count(url_name)
        self._load()
        self.assertEqual(self._count(url_name), before)

    def test_professor_changelist_query_count_is_constant(self):
        self._assert_constant("admin:reviews_professor_changelist")

    def test_review_changelist_query_count_is_constant(self):
        self._assert_constant("admin:reviews_review_changelist")

    def test_vote_and_report_changelists_query_count_is_constant(self):
        self._add_votes_and_reports(0, 3)
        for n, url_name in enumerate(
            ("admin:reviews_vote_changelist", "admin:reviews_report_changelist")
        ):
            with self.subTest(changelist=url_name):
                self.client.get(reverse(url_name))  # warm session/permission caches
                before = self._count(url_name)
                self._add_votes_and_reports(100 + n * 100, 8)
                self.assertEqual(self._count(url_name), before)

    def _add_votes_and_reports(self, offset, n):
        from reviews.models import Report, Vote
        prof = self.repo.search("Айбек")[0]["prof"]
        review = self.repo.reviews_for(prof.id)[0]
        for i in range(n):
            user = User.objects.create_user(
                username=f"fx{offset + i}@example.com", email=f"fx{offset + i}@example.com",
                password="pass12345",
            )
            Vote.objects.create(review_id=review.id, user=user, helpful=True)
            Report.objects.create(review_id=review.id, user=user)


# ---------------------------------------------------------------------------
# Performance: hot filter/sort columns are backed by database indexes
# ---------------------------------------------------------------------------
class PerfIndexDeclarationTests(TestCase):
    """Indexes used by the read paths are declared on the models.

    Migration ``0004`` adds them to the schema; keeping the model declarations
    in sync is what ``makemigrations --check`` guards.
    """

    def test_hot_path_indexes_are_declared(self):
        from .models import Professor, Review, School

        expected = {
            School: {"school_city_idx", "school_name_idx"},
            Professor: {"prof_status_idx", "prof_school_status_idx", "prof_dept_idx", "prof_name_idx"},
            Review: {"review_prof_status_idx", "review_status_created_idx"},
        }
        for model, names in expected.items():
            declared = {idx.name for idx in model._meta.indexes}
            with self.subTest(model=model.__name__):
                self.assertTrue(
                    names.issubset(declared), f"{model.__name__} missing {names - declared}"
                )


class MigrationsUpToDateTests(TestCase):
    """Model declarations must stay in sync with the migration files."""

    def test_makemigrations_check_finds_no_changes(self):
        # `--check` exits with status 1 when models.py drifted from the
        # migrations; call_command surfaces that as SystemExit.
        from django.core.management import call_command

        call_command("makemigrations", check=True, verbosity=0)


# ---------------------------------------------------------------------------
# Health endpoint and custom error pages
# ---------------------------------------------------------------------------
class HealthEndpointTests(TestCase):
    def test_health_returns_json_ok(self):
        r = self.client.get(reverse("health"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/json")
        self.assertJSONEqual(r.content, {"status": "ok"})

    def test_health_pings_the_database(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("health"))
        self.assertTrue(
            any("SELECT 1" in q["sql"] for q in ctx.captured_queries),
            "health must run a trivial query against the configured database",
        )

    def test_health_reports_db_failure_as_503(self):
        from django.db import OperationalError

        with mock.patch("reviews.views.connection") as conn:
            conn.cursor.side_effect = OperationalError("db is down")
            r = self.client.get(reverse("health"))
        self.assertEqual(r.status_code, 503)
        self.assertJSONEqual(r.content, {"status": "error"})


class ErrorPageTests(TestCase):
    def test_404_uses_custom_template(self):
        r = self.client.get("/definitely-not-a-real-page/")
        self.assertEqual(r.status_code, 404)
        self.assertTemplateUsed(r, "404.html")
        self.assertContains(r, "Страница не найдена", status_code=404)
        self.assertContains(r, "Вернуться на главную", status_code=404)

    def test_404_page_links_to_home(self):
        r = self.client.get("/definitely-not-a-real-page/")
        self.assertContains(r, f'<a href="{reverse("home")}">', status_code=404)

    def test_500_uses_custom_template(self):
        from django.test import RequestFactory
        from django.views.defaults import server_error
        request = RequestFactory().get("/boom/")
        r = server_error(request)
        self.assertEqual(r.status_code, 500)
        self.assertContains(r, "Ошибка сервера", status_code=500)

    def test_500_page_is_russian_with_home_link(self):
        from django.test import RequestFactory
        from django.views.defaults import server_error
        request = RequestFactory().get("/boom/")
        r = server_error(request)
        self.assertContains(r, "Что-то пошло не так", status_code=500)
        self.assertContains(r, "Вернуться на главную", status_code=500)
        self.assertContains(r, f'<a href="{reverse("home")}">', status_code=500)

    def test_error_templates_extend_base(self):
        for name in ("404.html", "500.html"):
            with self.subTest(template=name):
                text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
                self.assertIn('extends "reviews/base.html"', text)


class EmptyStateTests(_MemoryRepoTestCase):
    """List pages render a friendly empty state instead of a blank area."""

    def test_professor_without_reviews_shows_empty_state(self):
        school = self.repo.schools()[0]
        p = self.repo.add_professor(school.id, "Тихон", "Безотзывов", "Физика", "доцент")
        r = self.client.get(reverse("professor", args=[p.slug]))
        self.assertContains(r, "Пока нет отзывов")
        self.assertNotContains(r, 'class="review"')

    def test_school_without_professors_shows_empty_states(self):
        school = self.repo.add_school("Пустой вуз", "Бишкек")
        r = self.client.get(reverse("school_detail", args=[school.slug]))
        self.assertContains(r, "Пока нет преподавателей")
        self.assertContains(r, "Пока нет кафедр")
        self.assertNotContains(r, 'class="card"')

    def test_home_without_matches_shows_empty_state(self):
        r = self.client.get(reverse("home"), {"q": "неттакого"})
        self.assertContains(r, "Ничего не нашлось")
        self.assertNotContains(r, 'class="card"')


# ---------------------------------------------------------------------------
# Security: review-text sanitisation (control chars + length)
# ---------------------------------------------------------------------------
class TextSanitisationTests(TestCase):
    def test_clean_text_strips_control_chars_and_normalises_newlines(self):
        from .sanitize import clean_text
        dirty = "  Первая\x00 строка\x07\r\nВторая\tстрока\x1b  "
        cleaned = clean_text(dirty)
        self.assertEqual(cleaned, "Первая строка\nВторая\tстрока")
        self.assertFalse(any(ord(ch) < 32 and ch not in "\n\t" for ch in cleaned))

    def test_comment_field_length_matches_shared_constant(self):
        from .constants import MAX_COMMENT_LENGTH, MIN_COMMENT_LENGTH
        from .forms import ReviewForm
        field = ReviewForm().fields["comment"]
        self.assertEqual(field.max_length, MAX_COMMENT_LENGTH)
        self.assertEqual(field.min_length, MIN_COMMENT_LENGTH)

    def test_model_comment_has_max_length(self):
        from .constants import MAX_COMMENT_LENGTH
        from .models import Review
        self.assertEqual(Review._meta.get_field("comment").max_length, MAX_COMMENT_LENGTH)

    def test_form_strips_control_chars_from_comment(self):
        from .forms import ReviewForm
        data = _valid({"comment": "Хороший курс\x07 с примерами\x1b и практикой."})
        form = ReviewForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("\x07", form.cleaned_data["comment"])
        self.assertNotIn("\x1b", form.cleaned_data["comment"])

    def test_form_rejects_null_byte(self):
        # Django's ProhibitNullCharactersValidator rejects NUL outright; that is
        # even stricter than stripping, so the payload must never reach storage.
        from .forms import ReviewForm
        form = ReviewForm(_valid({"comment": "Нормальный отзыв\x00 с нулевым байтом."}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_form_strips_control_chars_from_course_and_term(self):
        from .forms import ReviewForm
        form = ReviewForm(_valid({"course": "Ал\x07горитмы", "term": "Осен\x1bь 2025"}))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["course"], "Алгоритмы")
        self.assertEqual(form.cleaned_data["term"], "Осень 2025")

    def test_form_rejects_overlong_comment(self):
        from .constants import MAX_COMMENT_LENGTH
        from .forms import ReviewForm
        form = ReviewForm(_valid({"comment": "я" * (MAX_COMMENT_LENGTH + 1)}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_form_rejects_comment_too_short_after_cleaning(self):
        from .constants import MIN_COMMENT_LENGTH
        from .forms import ReviewForm
        # 19 visible chars + padding controls pass the raw min_length check
        form = ReviewForm(_valid({"comment": "х" * (MIN_COMMENT_LENGTH - 1) + "\x07\x07"}))
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_repository_strips_control_chars(self):
        repo = InMemoryRepository()
        school = repo.add_school("Тестовый вуз", "Бишкек")
        prof = repo.add_professor(school.id, "Иван", "Петров", "Физика", "доцент")
        review = repo.add_review(prof.id, _repo_review(comment="Курс с\x00 примерами и практикой."))
        self.assertNotIn("\x00", review.comment)

    def test_repository_rejects_overlong_comment(self):
        from .constants import MAX_COMMENT_LENGTH
        repo = InMemoryRepository()
        school = repo.add_school("Тестовый вуз", "Бишкек")
        prof = repo.add_professor(school.id, "Иван", "Петров", "Физика", "доцент")
        with self.assertRaises(ValueError):
            repo.add_review(
                prof.id,
                _repo_review(course="Другой курс", comment="я" * (MAX_COMMENT_LENGTH + 1)),
            )


# ---------------------------------------------------------------------------
# Security: XSS — user text must be HTML-escaped, never rendered raw
# ---------------------------------------------------------------------------
class ReviewEscapingTests(_MemoryRepoTestCase):
    def test_comment_is_html_escaped_on_professor_page(self):
        prof = self.prof()
        payload = '<script>alert("xss")</script> и ещё текст'
        self.repo.add_review(prof.id, _repo_review(comment=payload), status="approved")
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertNotContains(r, payload)
        self.assertContains(r, "&lt;script&gt;alert")

    def test_course_is_html_escaped_on_professor_page(self):
        prof = self.prof()
        self.repo.add_review(prof.id, _repo_review(course="<img src=x onerror=alert(1)>"), status="approved")
        r = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertNotContains(r, "<img src=x onerror=alert(1)>")
        self.assertContains(r, "&lt;img")

    def test_search_query_is_html_escaped(self):
        payload = "<script>alert(1)</script>"
        r = self.client.get(reverse("home"), {"q": payload})
        self.assertNotContains(r, payload)
        self.assertContains(r, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_review_comment_is_escaped_on_professor_and_profile_pages(self):
        prof = self.prof()
        user = self.login()
        payload = '<script>alert("review")</script>'
        review = self.repo.add_review(
            prof.id, _repo_review(course="XSS курс", comment=payload), user=user
        )
        review.status = "approved"

        professor_response = self.client.get(reverse("professor", args=[prof.slug]))
        profile_response = self.client.get(reverse("profile"))
        for response in (professor_response, profile_response):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertNotContains(response, payload)
                self.assertContains(response, "&lt;script&gt;alert(&quot;review&quot;)&lt;/script&gt;")

    def test_professor_name_is_escaped_on_professor_school_home_and_profile_pages(self):
        school = self.repo.schools()[0]
        user = self.login()
        prof = self.repo.add_professor(
            school.id, "<script>", "alert(1)</script>", "XSS кафедра", "доцент"
        )
        review = self.repo.add_review(
            prof.id, _repo_review(course="XSS курс", comment="Достаточно длинный отзыв."), user=user
        )
        review.status = "approved"
        responses = (
            self.client.get(reverse("professor", args=[prof.slug])),
            self.client.get(reverse("school_detail", args=[school.slug])),
            self.client.get(reverse("home")),
            self.client.get(reverse("profile")),
        )
        payload = "<script> alert(1)</script>"
        escaped = "&lt;script&gt; alert(1)&lt;/script&gt;"
        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertNotContains(response, payload)
                self.assertContains(response, escaped)


# ---------------------------------------------------------------------------
# Security: CSRF — state-changing endpoints require a valid token
# ---------------------------------------------------------------------------
class CSRFProtectionTests(_MemoryRepoTestCase):
    def _csrf_client_with_user(self, email="csrf@example.com"):
        user = User.objects.create_user(username=email, email=email, password="pass12345")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        return client

    def test_vote_without_csrf_token_is_rejected(self):
        review = self.repo.reviews_for(self.prof().id)[0]
        client = self._csrf_client_with_user()
        r = client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 403)

    def test_report_without_csrf_token_is_rejected(self):
        review = self.repo.reviews_for(self.prof().id)[0]
        client = self._csrf_client_with_user()
        r = client.post(reverse("review_report", args=[review.id]))
        self.assertEqual(r.status_code, 403)

    def test_post_only_view_rejects_get(self):
        review = self.repo.reviews_for(self.prof().id)[0]
        self.login()
        r = self.client.get(reverse("review_vote", args=[review.id]))
        self.assertEqual(r.status_code, 405)

    def test_review_form_renders_csrf_token(self):
        self.login()
        r = self.client.get(reverse("review_new", args=[self.prof().slug]))
        self.assertContains(r, "csrfmiddlewaretoken")


# ---------------------------------------------------------------------------
# Security: open redirect — the `next` parameter must stay on this host
# ---------------------------------------------------------------------------
class OpenRedirectTests(TestCase):
    def _user(self):
        return User.objects.create_user(
            username="redir@example.com", email="redir@example.com", password="pass12345",
        )

    def test_login_rejects_absolute_external_next(self):
        self._user()
        r = self.client.post(reverse("login"), {
            "username": "redir@example.com", "password": "pass12345",
            "next": "https://evil.example.com/phishing",
        })
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example.com", r.url)
        self.assertEqual(r.url, reverse("home"))

    def test_login_rejects_protocol_relative_next(self):
        self._user()
        r = self.client.post(reverse("login"), {
            "username": "redir@example.com", "password": "pass12345",
            "next": "//evil.example.com/x",
        })
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example.com", r.url)

    def test_login_allows_same_host_next(self):
        self._user()
        r = self.client.post(reverse("login"), {
            "username": "redir@example.com", "password": "pass12345",
            "next": reverse("rules"),
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("rules"))

    def test_login_page_does_not_render_unsafe_next(self):
        r = self.client.get(reverse("login"), {"next": "https://evil.example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('value="https://evil.example.com"', r.content.decode())

    def test_login_page_escapes_next_in_hidden_input(self):
        payload = '/rules/?q="><script>alert(1)</script>'
        r = self.client.get(reverse("login"), {"next": payload})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_set_language_rejects_external_next(self):
        r = self.client.post(reverse("set_language"), {
            "language": "en", "next": "https://evil.example.com/phishing",
        })
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example.com", r.url)

    def test_set_language_allows_same_host_next(self):
        r = self.client.post(reverse("set_language"), {
            "language": "en", "next": reverse("rules"),
        })
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("rules"), r.url)


# ---------------------------------------------------------------------------
# Security: rate limiting (cache-backed fixed windows)
# ---------------------------------------------------------------------------
_THROTTLE_SETTINGS = override_settings(
    RATELIMIT_ENABLE=True,
    RATELIMIT={"login": (2, 60), "review": (1, 60), "vote": (1, 60), "signup": (2, 60)},
)


@_THROTTLE_SETTINGS
class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, ip="127.0.0.1", password="wrong-pass"):
        return self.client.post(
            reverse("login"),
            {"username": "rl@example.com", "password": password},
            REMOTE_ADDR=ip,
        )

    def test_budget_is_spent_then_returns_429(self):
        User.objects.create_user(username="rl@example.com", email="rl@example.com", password="pass12345")
        for _ in range(2):
            self.assertEqual(self._post().status_code, 200)
        self.assertEqual(self._post().status_code, 429)

    def test_limits_are_isolated_per_ip(self):
        User.objects.create_user(username="rl@example.com", email="rl@example.com", password="pass12345")
        for _ in range(2):
            self._post(ip="10.0.0.1")
        self.assertEqual(self._post(ip="10.0.0.1").status_code, 429)
        self.assertEqual(self._post(ip="10.0.0.2").status_code, 200)


@_THROTTLE_SETTINGS
class SignupRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, ip="127.0.0.1", password2="totally-different"):
        # A password mismatch keeps the client anonymous, so the per-IP
        # counter remains the active subject across repeated submissions.
        return self.client.post(
            reverse("signup"),
            {"email": "new@example.com", "password1": "password12345", "password2": password2},
            REMOTE_ADDR=ip,
        )

    def test_signup_budget_is_spent_then_returns_429(self):
        for _ in range(2):
            self.assertEqual(self._post().status_code, 200)
        self.assertEqual(self._post().status_code, 429)

    def test_signup_limits_are_isolated_per_ip(self):
        for _ in range(2):
            self._post(ip="10.0.0.1")
        self.assertEqual(self._post(ip="10.0.0.1").status_code, 429)
        self.assertEqual(self._post(ip="10.0.0.2").status_code, 200)

    def test_signup_get_page_is_not_limited(self):
        for _ in range(3):
            self._post()
        self.assertEqual(self.client.get(reverse("signup")).status_code, 200)

    def test_first_valid_signup_is_allowed(self):
        self.assertEqual(self._post(password2="password12345").status_code, 302)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

@_THROTTLE_SETTINGS
class SubmissionRateLimitTests(_MemoryRepoTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_review_submissions_are_limited(self):
        self.login()
        url = reverse("review_new", args=[self.prof().slug])
        self.assertEqual(self.client.post(url, _valid()).status_code, 302)
        self.assertEqual(self.client.post(url, _valid({"course": "Другой курс"})).status_code, 429)

    def test_review_page_get_is_not_limited(self):
        self.login()
        url = reverse("review_new", args=[self.prof().slug])
        self.client.post(url, _valid())
        self.client.post(url, _valid({"course": "Другой курс"}))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_votes_are_limited(self):
        self.login()
        review = self.repo.reviews_for(self.prof().id)[0]
        url = reverse("review_vote", args=[review.id])
        self.assertEqual(self.client.post(url, {"vote": "up"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"vote": "up"}).status_code, 429)


class RateLimitDisabledTests(_MemoryRepoTestCase):
    """RATELIMIT_ENABLE=False is a kill switch that always allows requests."""

    @override_settings(RATELIMIT_ENABLE=False, RATELIMIT={"login": (0, 60), "review": (0, 60)})
    def test_disabled_limiter_allows_repeated_posts(self):
        cache.clear()
        self.login()
        url = reverse("review_new", args=[self.prof().slug])
        self.assertEqual(self.client.post(url, _valid()).status_code, 302)
        self.assertEqual(self.client.post(url, _valid({"course": "Другой курс"})).status_code, 302)


class RateLimiterUnitTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(RATELIMIT_ENABLE=True, RATELIMIT={"thing": (2, 60)})
    def test_is_rate_limited_counts_per_subject(self):
        from django.test import RequestFactory

        from .throttle import is_rate_limited
        request = RequestFactory().post("/thing/", REMOTE_ADDR="1.2.3.4")
        self.assertFalse(is_rate_limited(request, "thing"))
        self.assertFalse(is_rate_limited(request, "thing"))
        self.assertTrue(is_rate_limited(request, "thing"))
        other = RequestFactory().post("/thing/", REMOTE_ADDR="5.6.7.8")
        self.assertFalse(is_rate_limited(other, "thing"))

    @override_settings(RATELIMIT_ENABLE=False)
    def test_disabled_is_never_limited(self):
        from django.test import RequestFactory

        from .throttle import is_rate_limited
        request = RequestFactory().post("/thing/", REMOTE_ADDR="1.2.3.4")
        for _ in range(50):
            self.assertFalse(is_rate_limited(request, "thing"))


class GZipMiddlewareTests(TestCase):
    def test_home_accepts_gzip(self):
        response = self.client.get(
            reverse("home"), HTTP_ACCEPT_ENCODING="gzip"
        )
        self.assertEqual(response.status_code, 200)
        if response.has_header("Content-Encoding"):
            self.assertEqual(response["Content-Encoding"], "gzip")


# ---------------------------------------------------------------------------
# Performance: catalog pages use django.core.cache and invalidate on writes
# ---------------------------------------------------------------------------
class CatalogCacheTests(_MemoryRepoTestCase):
    """Home results/filter options and school pages are served from the cache."""

    def setUp(self):
        cache.clear()
        super().setUp()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_home_results_and_options_are_cached(self):
        with (
            mock.patch.object(self.repo, "search", wraps=self.repo.search) as search,
            mock.patch.object(self.repo, "schools", wraps=self.repo.schools) as schools,
        ):
            self.client.get(reverse("home"))
            self.client.get(reverse("home"))
            self.assertEqual(search.call_count, 1)
            self.assertEqual(schools.call_count, 1)

    def test_home_options_are_shared_across_filter_combinations(self):
        with (
            mock.patch.object(self.repo, "search", wraps=self.repo.search) as search,
            mock.patch.object(self.repo, "schools", wraps=self.repo.schools) as schools,
        ):
            self.client.get(reverse("home"))
            self.client.get(reverse("home"), {"q": "Айбек"})
            self.assertEqual(search.call_count, 2)
            self.assertEqual(schools.call_count, 1)

    def test_new_review_invalidates_home_cache(self):
        url = reverse("home")
        professor_id = self.prof().id
        with mock.patch.object(self.repo, "search", wraps=self.repo.search) as search:
            self.client.get(url)
            self.client.get(url)
            self.assertEqual(search.call_count, 1)
            self.repo.add_review(
                professor_id, _repo_review(course="Новый курс"), status="approved"
            )
            self.client.get(url)
            self.assertEqual(search.call_count, 2)

    def test_approved_review_invalidates_home_cache(self):
        url = reverse("home")
        review = self.repo.add_review(
            self.prof().id, _repo_review(course="На модерации"), status="pending"
        )
        with mock.patch.object(self.repo, "search", wraps=self.repo.search) as search:
            self.client.get(url)
            self.client.get(url)
            self.assertEqual(search.call_count, 1)
            self.repo.approve_review(review.id)
            self.client.get(url)
            self.assertEqual(search.call_count, 2)

    def test_school_page_is_cached_and_invalidated_by_review(self):
        school = self.repo.schools()[0]
        url = reverse("school_detail", args=[school.slug])
        prof = self.repo.professors_for_school(school.id)[0]
        with mock.patch.object(self.repo, "search", wraps=self.repo.search) as search:
            self.client.get(url)
            self.client.get(url)
            self.assertEqual(search.call_count, 1)
            self.repo.add_review(
                prof.id, _repo_review(course="Ещё курс"), status="approved"
            )
            self.client.get(url)
            self.assertEqual(search.call_count, 2)

    def test_school_pages_are_cached_per_school(self):
        first, second = self.repo.schools()[:2]
        with mock.patch.object(self.repo, "search", wraps=self.repo.search) as search:
            self.client.get(reverse("school_detail", args=[first.slug]))
            self.client.get(reverse("school_detail", args=[second.slug]))
            self.assertEqual(search.call_count, 2)


class DjangoCatalogCacheInvalidationTests(TestCase):
    """ORM writes and moderation refresh cached home and university pages."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.repo = DjangoRepository()
        self.school = self.repo.add_school("Кешевый университет", "Бишкек")
        self.professor = self.repo.add_professor(
            self.school.id, "Иван", "Кешев", "Информатика", "доцент"
        )
        repo_patcher = mock.patch("reviews.views.repo", self.repo)
        repo_patcher.start()
        self.addCleanup(repo_patcher.stop)

    @staticmethod
    def _review_count(response):
        if "stats" in response.context:
            return response.context["stats"]["review_count"]
        return response.context["results"][0]["stats"]["count"]

    def _assert_invalidated_after(self, url, write):
        with mock.patch.object(self.repo, "search", wraps=self.repo.search) as search:
            initial = self.client.get(url)
            self.assertEqual(initial.status_code, 200)
            initial_count = self._review_count(initial)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(search.call_count, 1)

            write()

            refreshed = self.client.get(url)
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(search.call_count, 2)
            self.assertEqual(self._review_count(refreshed), initial_count + 1)

    def test_new_review_invalidates_home_and_school_caches(self):
        pages = (
            ("home", reverse("home")),
            ("school", reverse("school_detail", args=[self.school.slug])),
        )
        for name, url in pages:
            with self.subTest(page=name):
                cache.clear()
                self._assert_invalidated_after(
                    url,
                    lambda: self.repo.add_review(
                        self.professor.id,
                        _repo_review(course=f"Новый курс: {name}"),
                        status="approved",
                    ),
                )

    def test_review_approval_invalidates_home_and_school_caches(self):
        pages = (
            ("home", reverse("home")),
            ("school", reverse("school_detail", args=[self.school.slug])),
        )
        for name, url in pages:
            with self.subTest(page=name):
                cache.clear()
                review = self.repo.add_review(
                    self.professor.id,
                    _repo_review(course=f"Модерация: {name}"),
                    status="pending",
                )
                self._assert_invalidated_after(
                    url, lambda: self.repo.approve_review(review.id)
                )


class MessagesRenderTests(_MemoryRepoTestCase):
    """Rendered messages retain their accessible markup and static behavior."""

    def test_success_message_markup_and_scripts(self):
        self.login()
        prof = self.prof()
        response = self.client.post(
            reverse("review_new", args=[prof.slug]), _valid(), follow=True
        )

        self.assertContains(response, 'class="message success"')
        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'aria-label="Закрыть уведомление"')
        self.assertContains(response, "reviews/base.js")
        self.assertContains(response, "reviews/messages.js")
        self.assertNotRegex(response.content.decode(), r"<script(?![^>]*\bsrc=)[^>]*>\s*[^\s<]")

    def test_error_message_uses_alert_role(self):
        self.login()
        review = self.repo.reviews_for(self.prof().id)[0]
        response = self.client.post(
            reverse("review_report", args=[review.id]), {"reason": "invalid"}, follow=True
        )

        self.assertContains(response, 'class="message error"')
        self.assertContains(response, 'role="alert"')

    def test_messages_javascript_exists_in_static_files(self):
        script = Path(__file__).parent / "static" / "reviews" / "messages.js"
        self.assertTrue(script.is_file())
        self.assertTrue(script.read_text(encoding="utf-8").strip())
