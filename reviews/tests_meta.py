from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from . import store as store_mod
from .repository import InMemoryRepository
from .seed import seed

DEFAULT_DESCRIPTION = (
    "ProfLens — отзывы о преподавателях вузов Кыргызстана: "
    "рейтинги, сложность курсов и советы студентов."
)


class MetaTagsTestMixin:
    def setUp(self):
        import reviews.views as views

        self._orig_store_repo = store_mod.repo
        self._orig_views_repo = views.repo
        self.repo = InMemoryRepository()
        seed(self.repo)
        store_mod.repo = self.repo
        views.repo = self.repo

    def tearDown(self):
        import reviews.views as views

        store_mod.repo = self._orig_store_repo
        views.repo = self._orig_views_repo

    def html(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class HomeMetaTagsTests(MetaTagsTestMixin, TestCase):
    def test_default_meta_tags_are_rendered(self):
        html = self.html(reverse("home"))
        self.assertIn("<title>ProfLens</title>", html)
        self.assertIn(
            f'<meta name="description" content="{DEFAULT_DESCRIPTION}">', html
        )
        self.assertIn('<meta property="og:title" content="ProfLens">', html)
        self.assertIn(
            f'<meta property="og:description" content="{DEFAULT_DESCRIPTION}">', html
        )

    def test_meta_blocks_can_be_overridden(self):
        template = Template(
            '{% extends "reviews/base.html" %}'
            "{% block title %}Custom title{% endblock %}"
            "{% block description %}Custom description{% endblock %}"
            "{% block og_title %}Custom og title{% endblock %}"
            "{% block og_description %}Custom og description{% endblock %}"
        )
        html = template.render(Context({}))
        self.assertIn("<title>Custom title</title>", html)
        self.assertIn('<meta name="description" content="Custom description">', html)
        self.assertIn('<meta property="og:title" content="Custom og title">', html)
        self.assertIn(
            '<meta property="og:description" content="Custom og description">', html
        )


class ProfessorMetaTagsTests(MetaTagsTestMixin, TestCase):
    def test_professor_meta_tags_describe_the_professor(self):
        row = self.repo.search("")[0]
        prof = row["prof"]
        school = self.repo.school(prof.school_id)
        html = self.html(reverse("professor", args=[prof.slug]))

        title = f"{prof.full_name} — ProfLens"
        description = (
            f"Отзывы студентов о преподавателе {prof.full_name} "
            f"({prof.department}, {school.name}): рейтинг, сложность и советы."
        )
        self.assertIn(f"<title>{title}</title>", html)
        self.assertIn(f'<meta property="og:title" content="{title}">', html)
        self.assertIn(f'<meta name="description" content="{description}">', html)
        self.assertIn(f'<meta property="og:description" content="{description}">', html)


class SchoolMetaTagsTests(MetaTagsTestMixin, TestCase):
    def test_school_meta_tags_describe_the_school(self):
        school = self.repo.schools()[0]
        html = self.html(reverse("school_detail", args=[school.slug]))

        title = f"{school.name} — ProfLens"
        description = (
            f"Преподаватели и отзывы студентов вуза {school.name} "
            f"({school.city}): рейтинги, кафедры и советы."
        )
        self.assertIn(f"<title>{title}</title>", html)
        self.assertIn(f'<meta property="og:title" content="{title}">', html)
        self.assertIn(f'<meta name="description" content="{description}">', html)
        self.assertIn(f'<meta property="og:description" content="{description}">', html)