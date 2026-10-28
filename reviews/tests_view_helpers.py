"""Focused tests for the shared helpers used by the HTTP views."""
from types import SimpleNamespace

from django import forms
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from .views import _is_author, _is_valid_post, _review_form_context


class _SampleForm(forms.Form):
    name = forms.CharField()


class IsAuthorTests(SimpleTestCase):
    def test_matching_ids_are_compared_as_strings(self):
        review = SimpleNamespace(author_id=7)
        self.assertTrue(_is_author(review, SimpleNamespace(id="7")))

    def test_different_ids_are_not_the_author(self):
        review = SimpleNamespace(author_id=7)
        self.assertFalse(_is_author(review, SimpleNamespace(id=8)))

    def test_missing_author_is_never_the_author(self):
        review = SimpleNamespace(author_id=None)
        self.assertFalse(_is_author(review, SimpleNamespace(id=None)))
        self.assertFalse(_is_author(review, SimpleNamespace(id=7)))


class IsValidPostTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_request_is_not_a_valid_post(self):
        request = self.factory.get("/")
        self.assertFalse(_is_valid_post(request, _SampleForm()))

    def test_valid_post_is_accepted(self):
        request = self.factory.post("/", {"name": "ok"})
        self.assertTrue(_is_valid_post(request, _SampleForm(request.POST)))

    def test_invalid_post_is_rejected(self):
        request = self.factory.post("/", {})
        self.assertFalse(_is_valid_post(request, _SampleForm(request.POST)))


class ReviewFormContextTests(SimpleTestCase):
    def setUp(self):
        self.prof = SimpleNamespace(slug="prof")
        self.form = _SampleForm()

    def test_new_review_context_includes_account_urls(self):
        context = _review_form_context(self.prof, self.form)

        self.assertEqual(context["url"], reverse("rules"))
        self.assertEqual(context["login_url"], reverse("login"))
        self.assertEqual(context["signup_url"], reverse("signup"))
        self.assertNotIn("editing", context)

    def test_edit_review_context_marks_editing_and_omits_account_urls(self):
        context = _review_form_context(self.prof, self.form, editing=True)

        self.assertEqual(context["url"], reverse("rules"))
        self.assertTrue(context["editing"])
        self.assertNotIn("login_url", context)
        self.assertNotIn("signup_url", context)
