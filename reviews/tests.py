from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from professors.models import Course, Professor
from reviews.admin import publish_reviews, resolve_reports
from reviews.models import Review, ReviewReport
from universities.models import Department, University


class ReviewModelTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.author = self.user_model.objects.create_user(username='student', password='password')
        self.second_author = self.user_model.objects.create_user(username='student2', password='password')
        self.moderator = self.user_model.objects.create_user(username='moderator', password='password', is_staff=True)
        self.university = University.objects.create(name='Kyrgyz National University', slug='knu', city='Bishkek')
        self.department = Department.objects.create(
            university=self.university,
            name='Computer Science',
            slug='computer-science',
        )
        self.professor = Professor.objects.create(first_name='Aida', last_name='Osmonova', slug='aida-osmonova')
        self.professor.departments.add(self.department)
        self.course = Course.objects.create(
            university=self.university,
            department=self.department,
            code='CS101',
            name='Intro to Programming',
        )
        self.course.professors.add(self.professor)

    def make_review(self, **overrides):
        values = {
            'professor': self.professor,
            'course': self.course,
            'author': self.author,
            'academic_year': '2025',
            'term': 'Fall',
            'rating': 4,
            'difficulty': 3,
            'would_take_again': True,
            'comment': 'Clear lectures and fair grading throughout the semester.',
        }
        values.update(overrides)
        return Review.objects.create(**values)

    def test_new_review_is_pending_and_not_public(self):
        review = self.make_review()

        self.assertEqual(review.status, Review.Status.PENDING)
        self.assertEqual(Review.objects.public().count(), 0)

    def test_review_validation_rejects_invalid_scores_and_short_comments(self):
        review = Review(
            professor=self.professor,
            course=self.course,
            author=self.author,
            academic_year='2025',
            rating=6,
            difficulty=0,
            would_take_again=True,
            comment='Too short.',
        )

        with self.assertRaises(ValidationError):
            review.full_clean()

    def test_published_summary_uses_only_published_reviews(self):
        first = self.make_review(author=self.author, rating=4, difficulty=3, would_take_again=True)
        first.publish(self.moderator)
        first.save()

        second = self.make_review(
            author=self.second_author,
            academic_year='2024',
            rating=2,
            difficulty=5,
            would_take_again=False,
        )
        second.publish(self.moderator)
        second.save()

        self.make_review(
            author=self.user_model.objects.create_user(username='pending', password='password'),
            academic_year='2023',
            rating=5,
            difficulty=1,
            would_take_again=True,
        )

        hidden = self.make_review(
            author=self.user_model.objects.create_user(username='hidden', password='password'),
            academic_year='2022',
            rating=1,
            difficulty=1,
            would_take_again=False,
        )
        hidden.hide(self.moderator)
        hidden.save()

        summary = self.professor.rating_summary()

        self.assertEqual(summary['review_count'], 2)
        self.assertEqual(summary['average_rating'], 3)
        self.assertEqual(summary['average_difficulty'], 4)
        self.assertEqual(summary['would_take_again_percent'], 50)

    def test_public_reviews_require_published_status_and_active_related_records(self):
        published = self.make_review()
        published.publish(self.moderator)
        published.save()

        self.assertEqual(list(Review.objects.public()), [published])

        published.hide(self.moderator)
        published.save()

        self.assertEqual(Review.objects.public().count(), 0)

        published.publish(self.moderator)
        published.save()
        self.professor.is_active = False
        self.professor.save()

        self.assertEqual(Review.objects.public().count(), 0)

    def test_duplicate_active_review_is_rejected_until_original_is_hidden(self):
        original = self.make_review()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_review(comment='Another long enough duplicate review comment.')

        original.hide(self.moderator)
        original.save()

        replacement = self.make_review(comment='Replacement review after moderation hid the original.')

        self.assertEqual(replacement.status, Review.Status.PENDING)

    def test_report_resolve_records_moderator_and_timestamp(self):
        review = self.make_review()
        review.publish(self.moderator)
        review.save()

        report = ReviewReport.objects.create(
            review=review,
            reporter=self.second_author,
            reason=ReviewReport.Reason.SPAM,
            details='Looks promotional.',
        )

        report.resolve(self.moderator)
        report.save()

        self.assertEqual(report.status, ReviewReport.Status.RESOLVED)
        self.assertEqual(report.resolved_by, self.moderator)
        self.assertIsNotNone(report.resolved_at)

    def test_admin_publish_action_updates_batch_without_overwriting_existing_published_at(self):
        request = SimpleNamespace(user=self.moderator)
        pending = self.make_review()
        already_published = self.make_review(author=self.second_author, academic_year='2024')
        existing_published_at = timezone.now() - timedelta(days=10)
        already_published.status = Review.Status.PUBLISHED
        already_published.published_at = existing_published_at
        already_published.save()

        publish_reviews(None, request, Review.objects.filter(pk__in=[pending.pk, already_published.pk]))

        pending.refresh_from_db()
        already_published.refresh_from_db()

        self.assertEqual(pending.status, Review.Status.PUBLISHED)
        self.assertEqual(pending.moderated_by, self.moderator)
        self.assertIsNotNone(pending.published_at)
        self.assertEqual(already_published.published_at, existing_published_at)

    def test_admin_resolve_reports_action_updates_batch(self):
        request = SimpleNamespace(user=self.moderator)
        review = self.make_review()
        report = ReviewReport.objects.create(
            review=review,
            reporter=self.second_author,
            reason=ReviewReport.Reason.OTHER,
        )

        resolve_reports(None, request, ReviewReport.objects.filter(pk=report.pk))

        report.refresh_from_db()

        self.assertEqual(report.status, ReviewReport.Status.RESOLVED)
        self.assertEqual(report.resolved_by, self.moderator)
        self.assertIsNotNone(report.resolved_at)
