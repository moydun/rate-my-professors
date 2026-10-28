"""Spot checks for the most visible UI strings in every catalog.

The broader catalog tests in ``tests_i18n`` guarantee that every wrapped
string has an entry.  This module pins a small set of key strings from the
main pages (home, rules, about, auth, professor, errors) to concrete
translations so a catalog can never silently regress to an empty or
untranslated value for a prominent label.
"""
from pathlib import Path

from django.test import SimpleTestCase

from .tests_i18n import parse_po

LOCALE_DIR = Path(__file__).parent.parent / "locale"

KEY_TRANSLATIONS = {
    "Узнайте, какие занятия на самом деле — до записи на курс.": {
        "ru": "Узнайте, какие занятия на самом деле — до записи на курс.",
        "ky": "Сабактар чындыгында кандай өтөрүн — жазылуудан мурда билип алыңыз.",
        "en": "Find out what a class is really like before you enroll.",
    },
    "Имя преподавателя или кафедра": {
        "ru": "Имя преподавателя или кафедра",
        "ky": "Окутуучунун аты же кафедра",
        "en": "Professor name or department",
    },
    "Найти": {
        "ru": "Найти",
        "ky": "Табуу",
        "en": "Find",
    },
    "Все вузы": {
        "ru": "Все вузы",
        "ky": "Бардык ЖОЖдор",
        "en": "All schools",
    },
    "Правила": {
        "ru": "Правила",
        "ky": "Эрежелер",
        "en": "Rules",
    },
    "О проекте": {
        "ru": "О проекте",
        "ky": "Долбоор жөнүндө",
        "en": "About",
    },
    "Вход": {
        "ru": "Вход",
        "ky": "Кирүү",
        "en": "Log in",
    },
    "Регистрация": {
        "ru": "Регистрация",
        "ky": "Катталуу",
        "en": "Sign up",
    },
    "Username": {
        "ru": "Имя пользователя",
        "ky": "Колдонуучунун аты",
        "en": "Username",
    },
    "Please enter a correct %(username)s and password. Note that both fields may be case-sensitive.": {
        "ru": "Введите правильные %(username)s и пароль. Оба поля могут быть чувствительны к регистру.",
        "ky": "Туура %(username)s жана сырсөздү киргизиңиз. Эки талаа тең регистрге сезимтал болушу мүмкүн.",
        "en": "Please enter a correct %(username)s and password. Note that both fields may be case-sensitive.",
    },
    "This account is inactive.": {
        "ru": "Этот аккаунт неактивен.",
        "ky": "Бул аккаунт активдүү эмес.",
        "en": "This account is inactive.",
    },
    "Оценить преподавателя": {
        "ru": "Оценить преподавателя",
        "ky": "Окутуучуну баалоо",
        "en": "Rate this professor",
    },
    "Отзыв появится после проверки модератором.": {
        "ru": "Отзыв появится после проверки модератором.",
        "ky": "Сын-пикир модератор текшергенден кийин чыгат.",
        "en": "The review will appear after a moderator approves it.",
    },
    "Страница не найдена": {
        "ru": "Страница не найдена",
        "ky": "Барак табылган жок",
        "en": "Page not found",
    },
    "Ошибка сервера": {
        "ru": "Ошибка сервера",
        "ky": "Сервер катасы",
        "en": "Server error",
    },
}


class KeyStringTranslationsTests(SimpleTestCase):
    """Key labels used on prominent pages resolve to translations in all locales."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogs = {
            lang: parse_po(LOCALE_DIR / lang / "LC_MESSAGES" / "django.po")
            for lang in ("ru", "ky", "en")
        }

    def test_key_strings_exist_with_expected_translation(self):
        for msgid, expected in KEY_TRANSLATIONS.items():
            for lang, value in expected.items():
                with self.subTest(lang=lang, msgid=msgid):
                    catalog = self.catalogs[lang]
                    self.assertIn(msgid, catalog, f"{lang} catalog is missing {msgid!r}")
                    self.assertTrue(catalog[msgid], f"empty {lang} translation for {msgid!r}")
                    self.assertEqual(catalog[msgid], value)

    def test_key_strings_are_localized_for_ky(self):
        for msgid, expected in KEY_TRANSLATIONS.items():
            with self.subTest(msgid=msgid):
                self.assertNotEqual(expected["ky"], msgid)
