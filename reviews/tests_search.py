"""Focused coverage for professor search, filters and pagination.

The repository contract mixin runs against both storage backends so search
semantics cannot drift between the in-memory and ORM implementations.
"""
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .django_repo import DjangoRepository
from .repository import InMemoryRepository
from .tests import _MemoryRepoTestCase, _repo_review


class SearchContractMixin:
    def _make(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repo = self._make()
        self.bishkek = self.repo.add_school("Первый вуз", "Бишкек")
        self.osh = self.repo.add_school("Второй вуз", "Ош")
        self.ivan = self.repo.add_professor(
            self.bishkek.id, "Иван", "Петров", "Физика", "доцент"
        )
        self.anna = self.repo.add_professor(
            self.bishkek.id, "Анна", "Смирнова", "Математика", "профессор"
        )
        self.alan = self.repo.add_professor(
            self.osh.id, "Алан", "Тьюринг", "Информатика", "доцент"
        )
        self.john = self.repo.add_professor(
            self.osh.id, "Джон", "О'Коннор", "Лингвистика", "доцент"
        )
        self.pending = self.repo.add_professor(
            self.bishkek.id, "Пётр", "Скрытый", "Физика", "доцент", status="pending"
        )
        self.repo.add_review(self.ivan.id, _repo_review(course="К1", overall=4),
                             status="approved")
        self.repo.add_review(self.anna.id, _repo_review(course="К2", overall=5),
                             status="approved")
        self.repo.add_review(self.alan.id, _repo_review(course="К3", overall=3),
                             status="approved")

    @property
    def approved_ids(self):
        return {self.ivan.id, self.anna.id, self.alan.id, self.john.id}

    def _ids(self, **kwargs):
        return {x["prof"].id for x in self.repo.search(**kwargs)}

    def test_empty_query_returns_all_approved(self):
        for q in ("", "   ", "\t\n"):
            with self.subTest(q=q):
                self.assertEqual(self._ids(q=q), self.approved_ids)

    def test_query_matches_name_and_department_case_insensitively(self):
        self.assertEqual(self._ids(q="пЕтРоВ"), {self.ivan.id})
        self.assertEqual(self._ids(q="ФИЗИКА"), {self.ivan.id})
        self.assertEqual(self._ids(q="о'Ко"), {self.john.id})
        self.assertEqual(self._ids(q="ИВАн ПЕТРОВ"), {self.ivan.id})

    def test_special_characters_are_literal_not_wildcards(self):
        # SQL LIKE wildcards, regex metacharacters and quote/injection payloads
        # must never broaden the result set.
        for q in ("%", "_", "%%", ".*", "[а-я]", "\\", "'; DROP TABLE professors; --"):
            with self.subTest(q=q):
                self.assertEqual(self._ids(q=q), set())

    def test_special_characters_match_only_when_present_literally(self):
        self.assertEqual(self._ids(q="'"), {self.john.id})

    def test_school_filter_returns_only_that_school(self):
        self.assertEqual(self._ids(school_slug=self.bishkek.slug),
                         {self.ivan.id, self.anna.id})
        self.assertEqual(self._ids(school_slug=self.osh.slug),
                         {self.alan.id, self.john.id})

    def test_surrounding_whitespace_is_trimmed_in_all_filters(self):
        self.assertEqual(self._ids(q="  петров  "), {self.ivan.id})
        self.assertEqual(self._ids(department="  Физика  "), {self.ivan.id})
        self.assertEqual(self._ids(city="  Бишкек  "),
                         {self.ivan.id, self.anna.id})
        self.assertEqual(self._ids(school_slug=f"  {self.bishkek.slug}  "),
                         {self.ivan.id, self.anna.id})

    def test_whitespace_only_filters_return_all_approved(self):
        for kwargs in (
            {"school_slug": "   "},
            {"city": "\t\n"},
            {"department": "  "},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(self._ids(**kwargs), self.approved_ids)

    def test_overlong_query_and_filter_values_return_empty(self):
        long_text = "я" * 10000
        for kwargs in (
            {"q": long_text},
            {"school_slug": "s" * 10000},
            {"city": long_text},
            {"department": long_text},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(self._ids(**kwargs), set())

    def test_unknown_school_city_and_department_return_empty(self):
        self.assertEqual(self._ids(school_slug="no-such-school"), set())
        self.assertEqual(self._ids(city="Атлантида"), set())
        self.assertEqual(self._ids(department="Несуществующая"), set())

    def test_query_and_filters_are_combined(self):
        self.assertEqual(
            self._ids(q="петров", school_slug=self.bishkek.slug, city="Бишкек",
                      department="Физика"),
            {self.ivan.id},
        )
        self.assertEqual(
            self._ids(q="петров", school_slug=self.osh.slug, city="Ош",
                      department="Информатика"),
            set(),
        )

    def test_invalid_sort_values_fall_back_to_rating_order(self):
        expected = [row["prof"].id for row in self.repo.search("", sort="rating")]
        for sort in ("", "RATING", "Reviews", "newest", "helpful",
                     "rating desc", "'; DROP TABLE professors; --"):
            with self.subTest(sort=sort):
                actual = [row["prof"].id for row in self.repo.search("", sort=sort)]
                self.assertEqual(actual, expected)

    def test_pending_professor_never_leaks_through_search_or_filters(self):
        self.assertNotIn(self.pending.id, self._ids())
        self.assertNotIn(self.pending.id, self._ids(q="Скрытый"))
        self.assertNotIn(self.pending.id, self._ids(department="Физика"))
        self.assertNotIn(self.pending.id, self._ids(school_slug=self.bishkek.slug))


class MemorySearchContractTests(SearchContractMixin, TestCase):
    def _make(self):
        return InMemoryRepository()


class DjangoSearchContractTests(SearchContractMixin, TestCase):
    def _make(self):
        return DjangoRepository()


class HomeSearchPaginationTests(_MemoryRepoTestCase):
    def test_home_search_input_has_aria_label(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, '<input type="search" name="q"')
        self.assertContains(response, 'aria-label="Поиск"')

    def _add_professors(self, count, last_prefix="Джонс"):
        school = self.repo.schools()[0]
        for i in range(count):
            self.repo.add_professor(
                school.id, f"Смит{i:02d}", f"{last_prefix}{i:02d}",
                "Пагинация", "доцент",
            )

    def test_home_and_school_pages_expose_mobile_viewport_and_labeled_search(self):
        home = self.client.get(reverse("home"))
        school = self.repo.schools()[0]
        school_response = self.client.get(reverse("school_detail", args=[school.slug]))

        for response in (home, school_response):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<meta name="viewport" content="width=device-width, initial-scale=1">')

        self.assertContains(home, 'aria-label="Поиск"')
        self.assertContains(home, 'aria-label="Город"')
        self.assertContains(home, 'aria-label="Вуз"')
        self.assertContains(home, 'aria-label="Кафедра"')
        self.assertContains(home, 'aria-label="Сортировка"')

    def test_home_search_matches_professor_name(self):
        response = self.client.get(reverse("home"), {"q": "Айбек Токтогулов"})

        self.assertEqual(response.status_code, 200)
        names = [row["prof"].full_name for row in response.context["results"]]
        self.assertEqual(names, ["Айбек Токтогулов"])

    def test_home_sort_by_rating_orders_results_by_average(self):
        school = self.repo.schools()[0]
        high = self.repo.add_professor(
            school.id, "Рейтинг", "Высокий", "Проверка сортировки", "доцент"
        )
        low = self.repo.add_professor(
            school.id, "Рейтинг", "Низкий", "Проверка сортировки", "доцент"
        )
        self.repo.add_review(high.id, _repo_review(course="Рейтинг 1", overall=5), status="approved")
        self.repo.add_review(low.id, _repo_review(course="Рейтинг 2", overall=2), status="approved")

        response = self.client.get(
            reverse("home"), {"department": "Проверка сортировки", "sort": "rating"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["prof"].id for row in response.context["results"]],
            [high.id, low.id],
        )

    def test_pagination_keeps_query_and_filters(self):
        self._add_professors(15)
        params = {"q": "смит", "department": "Пагинация"}
        first = self.client.get(reverse("home"), params)
        second = self.client.get(reverse("home"), {**params, "page": 2})

        self.assertEqual(first.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(len(first.context["results"]), 10)
        self.assertEqual(len(second.context["results"]), 5)
        for r in first.context["results"] + second.context["results"]:
            self.assertEqual(r["prof"].department, "Пагинация")
            self.assertTrue(r["prof"].full_name.startswith("Смит"))

        first_ids = {r["prof"].id for r in first.context["results"]}
        second_ids = {r["prof"].id for r in second.context["results"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 15)

    def test_pagination_links_encode_special_characters_in_query(self):
        self._add_professors(15, last_prefix="Джонс&Ко")
        q = "джонс&ко"
        response = self.client.get(reverse("home"), {"q": q})

        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        match = re.search(r'href="([^"]*page=2[^"]*)"', response.content.decode())
        self.assertIsNotNone(match)
        query = parse_qs(urlsplit(unescape(match.group(1))).query)
        self.assertEqual(query.get("q"), [q])
        self.assertEqual(query.get("page"), ["2"])

    def test_out_of_range_and_invalid_pages_fall_back(self):
        for page in ("999", "abc", "0", "-1"):
            with self.subTest(page=page):
                response = self.client.get(reverse("home"), {"page": page})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["results"])

    def test_out_of_range_page_for_filtered_query_stays_in_results(self):
        self._add_professors(15)
        params = {"q": "смит", "department": "Пагинация", "page": "999"}
        response = self.client.get(reverse("home"), params)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number,
                         response.context["page_obj"].paginator.num_pages)
        self.assertEqual(len(response.context["results"]), 5)
        for row in response.context["results"]:
            self.assertEqual(row["prof"].department, "Пагинация")
            self.assertTrue(row["prof"].full_name.startswith("Смит"))

    def test_out_of_range_page_with_no_results_is_empty(self):
        response = self.client.get(
            reverse("home"), {"q": "неттакого", "page": "999"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["results"]), [])
        self.assertContains(response, "Ничего не нашлось")

    def test_extremely_long_page_value_falls_back(self):
        response = self.client.get(reverse("home"), {"page": "9" * 5000})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["results"])

    def test_overlong_query_and_filter_values_show_empty_state(self):
        long_text = "я" * 10000
        for param in ("q", "school", "city", "department"):
            with self.subTest(param=param):
                response = self.client.get(reverse("home"), {param: long_text})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["results"]), [])
                self.assertContains(response, "Ничего не нашлось")

    def test_whitespace_only_filters_list_everyone(self):
        for params in (
            {"school": "   "},
            {"city": "\t\n"},
            {"department": "  "},
        ):
            with self.subTest(**params):
                response = self.client.get(reverse("home"), params)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["results"])

    def test_invalid_sort_values_keep_rating_order(self):
        expected = [
            row["prof"].id
            for row in self.client.get(reverse("home"), {"sort": "rating"}).context["results"]
        ]
        for sort in ("", "RATING", "Reviews", "newest", "helpful",
                     "rating desc", "'; DROP TABLE professors; --"):
            with self.subTest(sort=sort):
                response = self.client.get(reverse("home"), {"sort": sort})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["sort"], "rating")
                self.assertEqual(
                    [row["prof"].id for row in response.context["results"]],
                    expected,
                )

    def test_special_character_query_shows_empty_state(self):
        for q in ("%", "_", ".*", "'; DROP TABLE professors; --"):
            with self.subTest(q=q):
                response = self.client.get(reverse("home"), {"q": q})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["results"]), [])
                self.assertContains(response, "Ничего не нашлось")

    def test_unknown_filter_values_show_empty_state(self):
        for params in ({"school": "no-such-school"}, {"city": "Атлантида"},
                       {"department": "Несуществующая"}):
            with self.subTest(**params):
                response = self.client.get(reverse("home"), params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["results"]), [])
                self.assertContains(response, "Ничего не нашлось")


class HomeEmptyStateRenderTests(_MemoryRepoTestCase):
    """An empty search result renders a hint and a link that resets filters."""

    EMPTY_STATE_TEXTS = {
        "ru": ("Ничего не нашлось", "Попробуйте сократить запрос или изменить фильтры.",
               "Сбросить фильтры"),
        "ky": ("Эч нерсе табылган жок", "Суроону кыскартып же чыпкаларды өзгөртүп көрүңүз.",
               "Чыпкаларды тазалоо"),
        "en": ("Nothing found", "Try a shorter query or change the filters.",
               "Reset filters"),
    }

    def setUp(self):
        cache.clear()
        super().setUp()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _empty_response(self, **extra):
        return self.client.get(reverse("home"), {"q": "неттакого", **extra})

    def _reset_href(self, response):
        """Return the reset-filters link target, whatever the attribute order."""
        html = response.content.decode()
        anchor = re.search(r'<a\b[^>]*empty-state-action[^>]*>', html)
        self.assertIsNotNone(anchor, "empty state must render the reset-filters link")
        href = re.search(r'href="([^"]*)"', anchor.group(0))
        self.assertIsNotNone(href, "reset-filters link must carry an href")
        return unescape(href.group(1))

    def test_empty_results_render_hint_and_reset_link(self):
        response = self._empty_response()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["results"]), [])
        self.assertContains(response, 'role="status"')
        self.assertContains(response, "Ничего не нашлось")
        self.assertContains(response, "Попробуйте сократить запрос или изменить фильтры.")
        self.assertContains(response, ">Сбросить фильтры</a>")

    def test_reset_link_targets_home_without_query_parameters(self):
        response = self._empty_response(city="Ош", department="Несуществующая")

        target = urlsplit(self._reset_href(response))
        self.assertEqual(target.path, reverse("home"))
        self.assertEqual(target.query, "")

    def test_reset_link_leads_to_unfiltered_results(self):
        response = self._empty_response()

        reset = self.client.get(self._reset_href(response))

        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.context["results"])
        self.assertNotContains(reset, "Ничего не нашлось")
        self.assertNotContains(reset, "Сбросить фильтры")

    def test_empty_state_hidden_when_search_has_results(self):
        response = self.client.get(reverse("home"), {"q": "Айбек"})

        self.assertTrue(response.context["results"])
        self.assertNotContains(response, "Ничего не нашлось")
        self.assertNotContains(response, "Сбросить фильтры")

    def test_empty_state_rendered_in_every_supported_language(self):
        for lang, texts in self.EMPTY_STATE_TEXTS.items():
            with self.subTest(lang=lang):
                response = self.client.get(
                    reverse("home"), {"q": "неттакого"}, HTTP_ACCEPT_LANGUAGE=lang
                )
                self.assertEqual(response.status_code, 200)
                for text in texts:
                    self.assertContains(response, text)
                for other, other_texts in self.EMPTY_STATE_TEXTS.items():
                    if other == lang:
                        continue
                    for text in other_texts:
                        self.assertNotContains(response, text)
