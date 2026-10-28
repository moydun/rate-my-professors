"""Focused tests for :mod:`reviews.sanitize`.

The helpers deliberately do not strip HTML: review text is stored as-is and
Django's autoescaping renders it as text.  These tests therefore pin down
both halves of that contract - control-character hygiene in the sanitizer and
HTML/XSS escaping at the rendering boundary.
"""
from django.test import SimpleTestCase
from django.urls import reverse

from .constants import MAX_COMMENT_LENGTH, MIN_COMMENT_LENGTH
from .sanitize import clean_text, strip_control_chars
from .tests import _MemoryRepoTestCase, _repo_review


class StripControlCharsTests(SimpleTestCase):
    def test_removes_every_c0_control_except_tab_lf_and_cr(self):
        for code in range(0x20):
            char = chr(code)
            with self.subTest(code=code):
                expected = char if char in "\t\n\r" else ""
                self.assertEqual(strip_control_chars(f"a{char}b"), f"a{expected}b")

    def test_removes_del_and_all_c1_controls(self):
        for code in range(0x7F, 0xA0):
            char = chr(code)
            with self.subTest(code=code):
                self.assertEqual(strip_control_chars(f"a{char}b"), "ab")

    def test_only_control_characters_become_empty_string(self):
        value = "".join(chr(code) for code in range(0x20) if chr(code) not in "\t\n\r")
        value += "".join(chr(code) for code in range(0x7F, 0xA0))
        self.assertEqual(strip_control_chars(value), "")

    def test_empty_and_none_values(self):
        for value in ("", None):
            with self.subTest(value=value):
                self.assertEqual(strip_control_chars(value), "")

    def test_keeps_ordinary_text_html_and_unicode_untouched(self):
        value = '<b>Отзыв</b> про курс — 100% полезно! <img src="x.png"> 😀'
        self.assertEqual(strip_control_chars(value), value)

    def test_preserves_tab_lf_and_cr_verbatim(self):
        self.assertEqual(strip_control_chars("a\tb\nc\rd"), "a\tb\nc\rd")

    def test_is_idempotent(self):
        dirty = "a\x00b\x07c\x1b d\x7f"
        once = strip_control_chars(dirty)
        self.assertEqual(strip_control_chars(once), once)


class CleanTextTests(SimpleTestCase):
    def test_empty_none_and_whitespace_only_values_become_empty(self):
        for value in ("", None, "   ", "\t\n\r\n", " \t \n "):
            with self.subTest(value=value):
                self.assertEqual(clean_text(value), "")

    def test_control_characters_only_become_empty(self):
        value = " \x00\x07\x1b\r\n\x7f\x85 "
        self.assertEqual(clean_text(value), "")

    def test_normalizes_crlf_and_lone_cr_to_lf(self):
        self.assertEqual(clean_text("a\r\nb\rc\rd"), "a\nb\nc\nd")
        self.assertEqual(clean_text("a\r\n\r\nb"), "a\n\nb")

    def test_preserves_internal_tabs_and_newlines_but_trims_outer_space(self):
        self.assertEqual(clean_text(" \t a\tb\nc \n "), "a\tb\nc")

    def test_trims_unicode_whitespace_around_text(self):
        self.assertEqual(clean_text("\u00a0 отзыв \u00a0"), "отзыв")

    def test_removes_control_chars_without_inserting_spaces(self):
        self.assertEqual(clean_text("Сло\x00во\x07 и\x1b ещё"), "Слово и ещё")

    def test_html_markup_is_preserved_verbatim(self):
        payload = '<script>alert("xss")</script>'
        self.assertEqual(clean_text(payload), payload)

        formatting = "<b>жирный</b> и <img src=x onerror=alert(1)>"
        self.assertEqual(clean_text(formatting), formatting)

    def test_html_entities_are_not_decoded_or_reencoded(self):
        value = "&lt;script&gt; &amp; &quot;кавычки&quot;"
        self.assertEqual(clean_text(value), value)

    def test_control_chars_inside_markup_are_removed_and_newlines_normalized(self):
        self.assertEqual(
            clean_text('<a href="x"\x00>\r\nтекст\x07</a>'),
            '<a href="x">\nтекст</a>',
        )

    def test_minimum_length_is_a_form_concern_not_a_sanitizer_concern(self):
        value = "x" * (MIN_COMMENT_LENGTH - 1)
        self.assertEqual(clean_text(value), value)
        self.assertEqual(len(clean_text(value)), MIN_COMMENT_LENGTH - 1)

    def test_maximum_length_text_is_preserved_exactly(self):
        value = "я" * MAX_COMMENT_LENGTH
        self.assertEqual(clean_text(value), value)
        self.assertEqual(len(clean_text(value)), MAX_COMMENT_LENGTH)

    def test_over_maximum_text_is_not_silently_truncated(self):
        value = "я" * (MAX_COMMENT_LENGTH + 1)
        self.assertEqual(len(clean_text(value)), MAX_COMMENT_LENGTH + 1)

    def test_sanitizing_twice_changes_nothing(self):
        dirty = " \x00 Первая\r\nВторая\x07\tстрока \x1b "
        once = clean_text(dirty)
        self.assertEqual(clean_text(once), once)

    def test_plain_text_is_returned_unchanged(self):
        value = "Понятные лекции и честные экзамены, рекомендую."
        self.assertEqual(clean_text(value), value)


class ReviewTextXssTests(_MemoryRepoTestCase):
    def test_script_comment_is_escaped_when_rendered(self):
        prof = self.prof()
        payload = '<script>alert("xss")</script> и ещё текст'
        review = self.repo.add_review(
            prof.id, _repo_review(course="XSS курс", comment=payload), status="approved"
        )
        self.assertEqual(review.comment, payload)

        response = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertNotContains(response, payload)
        self.assertContains(response, "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;")

    def test_html_formatting_and_event_handlers_are_escaped_not_interpreted(self):
        prof = self.prof()
        payload = '<b>жирный</b><img src=x onerror=alert(1)>'
        self.repo.add_review(
            prof.id, _repo_review(course="HTML курс", comment=payload), status="approved"
        )

        response = self.client.get(reverse("professor", args=[prof.slug]))
        self.assertNotContains(response, "<b>жирный</b>")
        self.assertNotContains(response, "<img src=x onerror=alert(1)>")
        self.assertContains(response, "&lt;b&gt;жирный&lt;/b&gt;&lt;img")

    def test_control_characters_are_stripped_before_storage(self):
        prof = self.prof()
        review = self.repo.add_review(
            prof.id,
            _repo_review(
                course="Ал\x07горитмы",
                comment="Полезный\x00 курс с практикой\x1b и примерами.",
            ),
            status="approved",
        )
        self.assertEqual(review.course, "Алгоритмы")
        self.assertEqual(review.comment, "Полезный курс с практикой и примерами.")
