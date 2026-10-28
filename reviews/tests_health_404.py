import json

from django.test import TestCase, override_settings
from django.urls import reverse


class HealthEndpointTests(TestCase):
    """GET /health/ — liveness probe for the container orchestrator."""

    def test_health_returns_200_json_ok(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), {"status": "ok"})


@override_settings(DEBUG=False)
class Custom404PageTests(TestCase):
    """Custom 404.html is rendered (not Django's debug page) in every locale."""

    # (language code, expected translated heading from 404.html)
    CASES = [
        ("ru", "Страница не найдена"),
        ("ky", "Барак табылган жок"),
        ("en", "Page not found"),
    ]

    def _get_missing(self, lang):
        return self.client.get(
            "/no-such-page/", HTTP_ACCEPT_LANGUAGE=lang
        )

    def test_404_status_custom_template_and_content_per_language(self):
        for lang, heading in self.CASES:
            with self.subTest(language=lang):
                response = self._get_missing(lang)
                self.assertEqual(response.status_code, 404)
                self.assertTemplateUsed(response, "404.html")
                self.assertContains(response, heading, status_code=404)


class ErrorHandlerRenderTests(TestCase):
    """Rendered error handlers stay custom and the 500 page needs no context."""

    def test_handler404_renders_custom_template(self):
        from django.conf.urls import handler404
        from django.test import RequestFactory
        from django.utils import translation

        request = RequestFactory().get("/missing/", HTTP_ACCEPT_LANGUAGE="ru")
        with translation.override("ru"):
            response = handler404(request, Exception("missing"))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Вернуться на главную", status_code=404)

    def test_handler500_renders_custom_template_without_db_queries(self):
        from django.conf.urls import handler500
        from django.db import connection
        from django.test import RequestFactory
        from django.test.utils import CaptureQueriesContext
        from django.utils import translation

        request = RequestFactory().get("/boom/")
        with translation.override("ru"), CaptureQueriesContext(connection) as queries:
            response = handler500(request)
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "Ошибка сервера", status_code=500)
        self.assertContains(response, "Что-то пошло не так", status_code=500)
        self.assertContains(response, "Вернуться на главную", status_code=500)
        self.assertEqual(len(queries), 0)


class ProjectErrorHandlerWiringTests(TestCase):
    """config.urls must explicitly wire the project's 404/500 handlers."""

    def test_urlconf_exposes_project_handlers(self):
        from config import urls
        from reviews import views

        self.assertIs(urls.handler404, views.handler404)
        self.assertIs(urls.handler500, views.handler500)

    def test_project_handler404_renders_custom_template(self):
        from django.test import RequestFactory
        from django.utils import translation

        from config.urls import handler404

        request = RequestFactory().get("/missing/", HTTP_ACCEPT_LANGUAGE="ru")
        with translation.override("ru"):
            response = handler404(request, Exception("missing"))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Вернуться на главную", status_code=404)

    def test_project_handler404_works_without_exception_argument(self):
        from django.test import RequestFactory

        from config.urls import handler404

        response = handler404(RequestFactory().get("/missing/"))
        self.assertEqual(response.status_code, 404)

    def test_project_handler500_renders_custom_template_without_db_queries(self):
        from django.db import connection
        from django.test import RequestFactory
        from django.test.utils import CaptureQueriesContext
        from django.utils import translation

        from config.urls import handler500

        request = RequestFactory().get("/boom/")
        with translation.override("ru"), CaptureQueriesContext(connection) as queries:
            response = handler500(request)
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "Ошибка сервера", status_code=500)
        self.assertContains(response, "Что-то пошло не так", status_code=500)
        self.assertContains(response, "Вернуться на главную", status_code=500)
        self.assertEqual(len(queries), 0)
