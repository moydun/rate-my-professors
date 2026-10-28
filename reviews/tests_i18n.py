"""Completeness checks for the ky/ru/en translation catalogs.

Forms, view messages, validators and every template string (including
``blocktranslate``) must have entries in ``locale/<lang>/LC_MESSAGES/django.po``
so the UI never silently falls back to another language.  These tests parse the
catalogs directly (no gettext binary required) and also resolve strings through
Django's translation layer, including values loaded from the repository (titles,
tags and grades).
"""
import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from .repository import InMemoryRepository
from .seed import seed

APP_DIR = Path(__file__).parent
LOCALE_DIR = APP_DIR.parent / "locale"
TEMPLATES_DIR = APP_DIR / "templates"
HOME_TEMPLATE = TEMPLATES_DIR / "reviews" / "home.html"

GETTEXT_NAMES = {"_", "gettext", "gettext_lazy", "ngettext", "ngettext_lazy"}


def parse_po(path):
    """Return ``{msgid: msgstr}`` for a gettext ``.po`` file.

    Handles multi-line strings and (ignores) plural forms.  The header entry
    (empty msgid) is kept but not used by callers.
    """
    entries = {}
    msgid = None
    msgstr = None
    mode = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            if msgid is not None:
                entries[msgid] = msgstr
            msgid = ast.literal_eval(line[len("msgid "):])
            msgstr = None
            mode = "id"
        elif line.startswith("msgstr "):
            msgstr = ast.literal_eval(line[len("msgstr "):])
            mode = "str"
        elif line.startswith('"'):
            chunk = ast.literal_eval(line)
            if mode == "id":
                msgid += chunk
            elif mode == "str":
                msgstr = (msgstr or "") + chunk
        elif line.startswith("msgid_plural") or line.startswith("msgstr["):
            mode = "plural"
    if msgid is not None:
        entries[msgid] = msgstr
    return entries


def python_translatable_strings():
    """All literal strings passed to gettext-style calls in app modules."""
    found = set()
    for path in APP_DIR.glob("*.py"):
        if path.name.startswith("tests"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in GETTEXT_NAMES:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


def template_translatable_strings():
    """Simple ``{% translate "..." %}`` strings (blocktranslate excluded)."""
    found = set()
    pattern = re.compile(r"""\{%\s*(?:trans|translate)\s+("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*%\}""")
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            found.add(ast.literal_eval(match.group(1)))
    return found


def template_blocktranslate_strings():
    """``{% blocktranslate %}`` strings converted to gettext ``%(var)s`` form."""
    found = set()
    pattern = re.compile(
        r"\{%\s*blocktranslate(?:[^%]*)%\}(.*?)\{%\s*endblocktranslate\s*%\}",
        re.DOTALL,
    )
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            body = " ".join(match.group(1).split())
            found.add(re.sub(r"\{\{\s*(\w+)\s*\}\}", r"%(\1)s", body))
    return found


def all_translatable_strings():
    """Every gettext string referenced by Python modules and templates."""
    return (
        python_translatable_strings()
        | template_translatable_strings()
        | template_blocktranslate_strings()
    )


class CatalogCompletenessTests(TestCase):
    """Every source string is present and translated in all three catalogs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogs = {
            lang: parse_po(LOCALE_DIR / lang / "LC_MESSAGES" / "django.po")
            for lang in ("ky", "ru", "en")
        }

    def test_python_source_strings_present_with_translation(self):
        for lang in ("ky", "ru", "en"):
            catalog = self.catalogs[lang]
            for msgid in sorted(python_translatable_strings()):
                with self.subTest(lang=lang, msgid=msgid):
                    self.assertIn(msgid, catalog)
                    self.assertTrue(catalog[msgid], f"empty translation for {msgid!r}")

    def test_template_strings_present_with_translation(self):
        for lang in ("ky", "ru", "en"):
            catalog = self.catalogs[lang]
            for msgid in sorted(template_translatable_strings() | template_blocktranslate_strings()):
                with self.subTest(lang=lang, msgid=msgid):
                    self.assertIn(msgid, catalog)
                    self.assertTrue(catalog[msgid], f"empty translation for {msgid!r}")

    def test_all_catalogs_cover_the_same_msgids(self):
        expected = set(all_translatable_strings())
        for lang in ("ky", "ru", "en"):
            missing = expected - set(self.catalogs[lang])
            with self.subTest(lang=lang):
                self.assertEqual(missing, set(), f"missing from {lang} catalog")

    def test_catalogs_have_no_empty_translations(self):
        for lang in ("ky", "ru", "en"):
            for msgid, msgstr in self.catalogs[lang].items():
                if msgid == "":
                    continue
                with self.subTest(lang=lang, msgid=msgid):
                    self.assertTrue(msgstr, f"untranslated entry in {lang}: {msgid!r}")


class FormAndMessageTranslationTests(TestCase):
    """Specific forms/validators/messages resolve in Kyrgyz and Russian."""

    KY = {
        "Нельзя голосовать за свой отзыв.": "Өз сын-пикириңизге добуш бере албайсыз.",
        "Слишком много запросов. Попробуйте позже.": "Сурамдар өтө көп. Кийинчерээк аракет кылыңыз.",
        "Слишком много попыток входа. Попробуйте позже.": "Кирүү аракеттери өтө көп. Кийинчерээк аракет кылыңыз.",
        "Выберите не более 3 тегов.": "3төн ашык тег тандабаңыз.",
        "Отзыв должен содержать не менее %(n)d символов.": "Сын-пикир кеминде %(n)d белгиден турушу керек.",
        "Отзыв должен содержать не более %(n)d символов.": "Сын-пикир %(n)d белгиден ашпашы керек.",
        "Пароль должен быть не менее 8 символов.": "Сырсөз кеминде 8 белгиден турушу керек.",
        "Пароли не совпадают.": "Сырсөздөр дал келбейт.",
        "Вы уже голосовали за этот отзыв.": "Бул сын-пикирге добуш бергенсиз.",
    }

    def test_ky_translations_match(self):
        with translation.override("ky"):
            for msgid, expected in self.KY.items():
                with self.subTest(msgid=msgid):
                    self.assertEqual(translation.gettext(msgid), expected)

    def test_ky_plural_placeholder_is_preserved(self):
        with translation.override("ky"):
            rendered = translation.gettext("Отзыв должен содержать не менее %(n)d символов.") % {"n": 20}
            self.assertIn("20", rendered)

    def test_en_new_messages_translated(self):
        expected = {
            "Нельзя голосовать за свой отзыв.": "You cannot vote on your own review.",
            "Слишком много запросов. Попробуйте позже.": "Too many requests. Please try again later.",
            "Слишком много попыток входа. Попробуйте позже.": "Too many login attempts. Please try again later.",
        }
        with translation.override("en"):
            for msgid, value in expected.items():
                with self.subTest(msgid=msgid):
                    self.assertEqual(translation.gettext(msgid), value)


class HomeTemplateTranslationTests(TestCase):
    """Home page labels are wrapped for translation and resolve per locale."""

    HOME_LABELS = {
        "ru": (
            "Узнайте, какие занятия на самом деле — до записи на курс.",
            "Имя преподавателя или кафедра",
            "Поиск",
            "Город",
            "Все города",
            "Все вузы",
            "Все кафедры",
            "Сортировка",
            "По рейтингу",
            "По числу отзывов",
            "Найти",
        ),
        "ky": (
            "Сабактар чындыгында кандай өтөрүн — жазылуудан мурда билип алыңыз.",
            "Окутуучунун аты же кафедра",
            "Издөө",
            "Шаар",
            "Бардык шаарлар",
            "Бардык ЖОЖдор",
            "Бардык кафедралар",
            "Иреттөө",
            "Рейтинг боюнча",
            "Сын-пикирлердин саны боюнча",
            "Табуу",
        ),
        "en": (
            "Find out what a class is really like before you enroll.",
            "Professor name or department",
            "Search",
            "City",
            "All cities",
            "All schools",
            "All departments",
            "Sort",
            "By rating",
            "By number of reviews",
            "Find",
        ),
    }

    EMPTY_STATE = {
        "ru": ("Ничего не нашлось", "Попробуйте сократить запрос или изменить фильтры.", "Сбросить фильтры"),
        "ky": ("Эч нерсе табылган жок", "Суроону кыскартып же чыпкаларды өзгөртүп көрүңүз.", "Чыпкаларды тазалоо"),
        "en": ("Nothing found", "Try a shorter query or change the filters.", "Reset filters"),
    }

    def test_home_template_wraps_all_literal_text(self):
        source = HOME_TEMPLATE.read_text(encoding="utf-8")
        source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
        source = re.sub(
            r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
            "",
            source,
            flags=re.DOTALL,
        )
        source = re.sub(
            r"\{%\s*blocktranslate.*?%\}.*?\{%\s*endblocktranslate\s*%\}",
            "",
            source,
            flags=re.DOTALL,
        )
        source = re.sub(r"\{%.*?%\}", "", source, flags=re.DOTALL)
        source = re.sub(r"\{\{.*?\}\}", "", source, flags=re.DOTALL)
        source = re.sub(r"<[^>]+>", "", source)
        self.assertEqual(re.findall(r"\S+", source), [])

    def test_home_labels_are_localized(self):
        for lang, labels in self.HOME_LABELS.items():
            with self.subTest(lang=lang):
                response = self.client.get(
                    reverse("home"), HTTP_ACCEPT_LANGUAGE=lang
                )
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for label in labels:
                    self.assertIn(label, html)
                if lang != "ru":
                    self.assertNotIn("Все города", html)
                    self.assertNotIn("По числу отзывов", html)

    def test_home_empty_state_is_localized(self):
        for lang, labels in self.EMPTY_STATE.items():
            with self.subTest(lang=lang):
                response = self.client.get(
                    reverse("home"),
                    {"q": "zzz-no-such-professor"},
                    HTTP_ACCEPT_LANGUAGE=lang,
                )
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for label in labels:
                    self.assertIn(label, html)


class RenderedTemplateTranslationTests(TestCase):
    """Repository values shown in templates are translated, not left in Russian."""

    def _render(self, lang, include_reviews=True):
        prof = SimpleNamespace(
            full_name="Test Professor",
            title="старший преподаватель",
            department="Computer Science",
            slug="test-professor",
        )
        school = SimpleNamespace(name="Test School", slug="test-school")
        stats = {
            "count": 1,
            "overall": 4.5,
            "difficulty": 3,
            "wta_pct": 100,
            "tone": "good",
            "distribution": [{"score": 5, "count": 1, "pct": 100}],
            "top_tags": ["Интересные лекции", "Вдохновляет"],
        }
        review = SimpleNamespace(
            id="review-1",
            overall=5,
            course="Algorithms",
            term="",
            difficulty=3,
            would_take_again=True,
            grade="Ещё не знаю",
            comment="Great course.",
            tags=["Понятные критерии"],
            helpful=1,
            not_helpful=0,
            created_at=datetime(2025, 10, 25, 12, 0, tzinfo=timezone.utc),
        )
        request = RequestFactory().get("/professors/test-professor/")
        with translation.override(lang):
            return render_to_string(
                "reviews/professor.html",
                {
                    "prof": prof,
                    "school": school,
                    "stats": stats,
                    "reviews": [review] if include_reviews else [],
                    "sort": "newest",
                },
                request=request,
            )

    def test_professor_page_translates_titles_tags_and_grades(self):
        expected = {
            "en": [
                "senior lecturer",
                "Interesting lectures",
                "Inspiring",
                "Clear criteria",
                "Not sure yet",
                "Grade:",
            ],
            "ky": [
                "ага окутуучу",
                "Кызыктуу лекциялар",
                "Шыктандырат",
                "Түшүнүктүү критерийлер",
                "Азырынча билбейм",
                "Баасы:",
            ],
            "ru": [
                "старший преподаватель",
                "Интересные лекции",
                "Вдохновляет",
                "Понятные критерии",
                "Ещё не знаю",
                "Оценка:",
            ],
        }
        forbidden = {
            "en": ["старший преподаватель", "Интересные лекции", "Понятные критерии", "Ещё не знаю"],
            "ky": ["старший преподаватель", "Интересные лекции", "Понятные критерии", "Ещё не знаю"],
        }
        for lang, values in expected.items():
            with self.subTest(lang=lang):
                html = self._render(lang)
                for value in values:
                    self.assertIn(value, html)
                for value in forbidden.get(lang, []):
                    self.assertNotIn(value, html)

    def test_ratings_and_review_dates_use_locale_formats(self):
        expected = {
            "ru": ("4,5", "25 октября 2025 г.", "Дата отзыва:"),
            "ky": ("4.5", "25 Октябрь 2025 ж.", "Сын-пикирдин күнү:"),
            "en": ("4.5", "Oct. 25, 2025", "Review date:"),
        }
        for lang, (rating, review_date, label) in expected.items():
            with self.subTest(lang=lang):
                html = self._render(lang)
                self.assertIn(f">{rating}</div>", html)
                self.assertIn(label, html)
                self.assertIn(f">{review_date}</time>", html)
                self.assertIn('<time datetime="2025-10-25T', html)

    def test_professor_page_renders_empty_reviews_call_to_action(self):
        html = self._render("ru", include_reviews=False)
        self.assertIn('class="empty empty-state"', html)
        self.assertIn("Пока нет отзывов. Станьте первым, кто оценит Test Professor.", html)
        self.assertIn('href="/professors/test-professor/rate/"', html)


class KyrgyzPageRenderTests(TestCase):
    """Every public page renders its labels in Kyrgyz, not Russian."""

    def setUp(self):
        import reviews.views as views
        from reviews import store as store_mod

        self.repo = InMemoryRepository()
        seed(self.repo)
        self._orig_store = store_mod.repo
        self._orig_views = views.repo
        store_mod.repo = self.repo
        views.repo = self.repo

    def tearDown(self):
        import reviews.views as views
        from reviews import store as store_mod

        store_mod.repo = self._orig_store
        views.repo = self._orig_views

    def test_public_pages_render_in_kyrgyz(self):
        prof = self.repo.search("Айбек")[0]["prof"]
        school = next(
            row["school"] for row in self.repo.search() if row["prof"].id == prof.id
        )
        pages = {
            reverse("home"): ("Бардык шаарлар", "Издөө", "Табуу"),
            reverse("rules"): ("Эрежелер", "Эреже бузууну көрдүңүзбү?", "«Арыздануу»"),
            reverse("about"): ("Долбоор жөнүндө", "Бул кандай иштейт", "Байланыш"),
            reverse("login"): ("Кирүү", "Аккаунтуңуз жокпу? Катталыңыз"),
            reverse("signup"): ("Катталуу", "Аккаунтуңуз барбы? Кириңиз"),
            reverse("school_detail", args=[school.slug]): (
                "Башкы бет",
                "Кафедралар",
                "Окутуучулар",
            ),
            reverse("professor", args=[prof.slug]): (
                "Башкы бет",
                "Сын-пикирлер",
                "Окутуучуну баалоо",
            ),
        }
        for url, labels in pages.items():
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ACCEPT_LANGUAGE="ky")
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('<html lang="ky"', html)
                for label in labels:
                    self.assertIn(label, html)


class LanguageAlternatesTests(TestCase):
    def test_html_pages_have_language_and_alternate_links(self):
        for path in ("/", "/rules/", "/about/", "/accounts/login/", "/accounts/signup/"):
            for lang in ("ky", "ru", "en"):
                with self.subTest(path=path, lang=lang):
                    response = self.client.get(path, HTTP_ACCEPT_LANGUAGE=lang)
                    self.assertEqual(response.status_code, 200)
                    html = response.content.decode()
                    self.assertIn(f'<html lang="{lang}"', html)
                    for alternate in ("ky", "ru", "en"):
                        self.assertIn(f'hreflang="{alternate}"', html)
                        self.assertIn(f'language={alternate}', html)

    def test_localized_dates_and_numbers_are_rendered_for_each_locale(self):
        from django.utils.formats import localize

        for lang, expected in (("ru", "4,5"), ("ky", "4.5"), ("en", "4.5")):
            with self.subTest(lang=lang), translation.override(lang):
                self.assertEqual(localize(4.5), expected)
