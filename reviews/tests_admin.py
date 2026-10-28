from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .admin import ReviewAdmin
from .models import Professor, Review, School


class ReviewAdminModerationTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Тестовый университет", slug="test-university", city="Бишкек")
        self.professor = Professor.objects.create(
            school=self.school,
            first_name="Иван",
            last_name="Иванов",
            department="Информатика",
            title="Доцент",
        )
        self.regular = User.objects.create_user(
            username="regular@example.com", email="regular@example.com", password="pass12345"
        )
        self.staff = User.objects.create_user(
            username="staff@example.com", email="staff@example.com", password="pass12345", is_staff=True
        )
        self.staff.user_permissions.add(Permission.objects.get(codename="change_review"))
        self.superuser = User.objects.create_superuser(
            username="admin@example.com", email="admin@example.com", password="pass12345"
        )

    def _review(self, course="Алгоритмы", status="pending"):
        return Review.objects.create(
            professor=self.professor,
            course=course,
            overall=5,
            difficulty=3,
            would_take_again=True,
            grade="A",
            comment="Содержательный отзыв достаточной длины.",
            status=status,
        )

    def _changelist_url(self):
        return reverse("admin:reviews_review_changelist")

    def _run_action(self, action, reviews):
        return self.client.post(
            self._changelist_url(),
            {"action": action, "_selected_action": [str(review.pk) for review in reviews]},
        )

    def test_anonymous_cannot_access_admin(self):
        for url in (reverse("admin:index"), self._changelist_url()):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login/", response.url)

    def test_regular_user_cannot_access_admin_or_moderate(self):
        self.client.force_login(self.regular)
        for url in (reverse("admin:index"), self._changelist_url()):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login/", response.url)

        review = self._review()
        response = self._run_action("approve_selected", [review])
        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, "pending")

    def test_staff_with_permission_can_access_admin(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.get(self._changelist_url()).status_code, 200)

    def test_superuser_can_access_admin(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.get(self._changelist_url()).status_code, 200)

    def test_review_admin_registers_approve_and_reject_actions(self):
        review_admin = ReviewAdmin(Review, admin.site)
        self.assertIn("approve_selected", review_admin.actions)
        self.assertIn("reject_selected", review_admin.actions)

    def test_approve_action_moderates_only_selected_reviews(self):
        selected = self._review(course="Алгоритмы")
        untouched = self._review(course="Базы данных")

        self.client.force_login(self.staff)
        response = self._run_action("approve_selected", [selected])

        self.assertEqual(response.status_code, 302)
        selected.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(selected.status, "approved")
        self.assertEqual(untouched.status, "pending")

    def test_reject_action_moderates_only_selected_reviews(self):
        selected = self._review(course="Алгоритмы", status="approved")
        untouched = self._review(course="Базы данных", status="approved")

        self.client.force_login(self.superuser)
        response = self._run_action("reject_selected", [selected])

        self.assertEqual(response.status_code, 302)
        selected.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(selected.status, "rejected")
        self.assertEqual(untouched.status, "approved")
