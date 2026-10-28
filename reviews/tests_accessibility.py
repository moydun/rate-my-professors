from pathlib import Path
import re

from django.test import TestCase
from django.urls import reverse


class AccessibilityRenderTests(TestCase):
    def test_base_template_renders_skip_link_and_labeled_icon_buttons(self):
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="skip-link" href="#main-content"')
        self.assertContains(response, 'id="main-content" tabindex="-1"')
        self.assertContains(response, 'class="nav-toggle"')
        self.assertContains(response, 'aria-label="Меню"')
        self.assertContains(response, 'aria-hidden="true"')

    def test_rendered_pages_do_not_include_images_without_alt_text(self):
        for url_name in ("home", "about", "rules"):
            with self.subTest(page=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for image in re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE):
                    self.assertRegex(image, r"\balt\s*=", msg=f"Image missing alt: {image}")

    def test_stylesheet_has_visible_keyboard_focus(self):
        stylesheet = (
            Path(__file__).parent / "static" / "reviews" / "style.css"
        ).read_text()

        self.assertIn(":focus-visible", stylesheet)
        self.assertRegex(stylesheet, r":focus-visible\s*\{[^}]*outline:\s*3px")
