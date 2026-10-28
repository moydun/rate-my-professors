from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_login_failed
from django.test import TestCase
from django.urls import reverse

from .repository import InMemoryRepository
from .seed import seed


class SecurityLoggingTests(TestCase):
    def test_failed_login_is_logged_without_password(self):
        with self.assertLogs("security", level="WARNING") as cm:
            user_login_failed.send(
                sender=None,
                credentials={"username": "bob", "password": "s3cret"},
            )
        out = "\n".join(cm.output)
        self.assertIn("bob", out)
        self.assertNotIn("s3cret", out)

    def test_upload_limits_set(self):
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 2_621_440)
        self.assertEqual(settings.FILE_UPLOAD_MAX_MEMORY_SIZE, 2_621_440)

    def test_script_policy_disallows_inline_scripts(self):
        response = self.client.get(reverse("home"))
        policy = response["Content-Security-Policy"]
        script_src = next(directive for directive in policy.split(";") if directive.strip().startswith("script-src"))
        self.assertNotIn("'unsafe-inline'", script_src)

    def test_pages_have_no_inline_scripts(self):
        import reviews.views as views
        from reviews import store

        repository = InMemoryRepository()
        seed(repository)
        original_store_repo, original_view_repo = store.repo, views.repo
        store.repo = views.repo = repository
        try:
            professor = repository.search("Айбек")[0]["prof"]
            user = User.objects.create_user(username="security-test", password="test-password")
            self.client.force_login(user)
            urls = (
                reverse("home"),
                reverse("professor", args=[professor.slug]),
                reverse("review_new", args=[professor.slug]),
            )
            for url in urls:
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotRegex(response.content.decode(), r"<script\b(?![^>]*\bsrc\s*=)[^>]*>")
        finally:
            store.repo, views.repo = original_store_repo, original_view_repo
