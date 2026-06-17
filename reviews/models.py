from django.conf import settings
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinLengthValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count, Q
from django.utils import timezone

from professors.models import Course, Professor


class ReviewQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Review.Status.PUBLISHED)

    def public(self):
        return self.published().filter(
            professor__is_active=True,
            professor__departments__is_active=True,
            professor__departments__university__is_active=True,
        ).distinct()

    def for_professor(self, professor):
        return self.filter(professor=professor)

    def published_summary(self):
        summary = self.published().aggregate(
            average_rating=Avg('rating'),
            average_difficulty=Avg('difficulty'),
            review_count=Count('id'),
            would_take_again_count=Count('id', filter=Q(would_take_again=True)),
        )
        review_count = summary['review_count']

        if review_count:
            would_take_again_percent = round(summary['would_take_again_count'] * 100 / review_count)
        else:
            would_take_again_percent = None

        return {
            'average_rating': summary['average_rating'],
            'average_difficulty': summary['average_difficulty'],
            'review_count': review_count,
            'would_take_again_percent': would_take_again_percent,
        }


class Review(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PUBLISHED = 'published', 'Published'
        HIDDEN = 'hidden', 'Hidden'
        REJECTED = 'rejected', 'Rejected'

    professor = models.ForeignKey(Professor, on_delete=models.CASCADE, related_name='reviews')
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, related_name='reviews', blank=True, null=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    academic_year = models.CharField(max_length=20, blank=True)
    term = models.CharField(max_length=40, blank=True)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    difficulty = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    would_take_again = models.BooleanField()
    comment = models.TextField(validators=[MinLengthValidator(20), MaxLengthValidator(2000)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    moderation_note = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='moderated_reviews',
        blank=True,
        null=True,
    )
    moderated_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(blank=True, null=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ['-published_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['author', 'professor', 'course', 'academic_year'],
                condition=Q(status__in=['pending', 'published'], course__isnull=False),
                name='unique_active_review_per_course_year',
            ),
            models.UniqueConstraint(
                fields=['author', 'professor', 'academic_year'],
                condition=Q(status__in=['pending', 'published'], course__isnull=True),
                name='unique_active_review_per_professor_year',
            ),
        ]

    def __str__(self):
        return f'{self.professor} review by {self.author}'

    @property
    def is_public(self):
        return (
            self.status == self.Status.PUBLISHED
            and self.professor.is_active
            and self.professor.departments.public().exists()
        )

    def publish(self, moderator):
        self.status = self.Status.PUBLISHED
        self.moderated_by = moderator
        self.moderated_at = timezone.now()
        if not self.published_at:
            self.published_at = self.moderated_at

    def hide(self, moderator):
        self.status = self.Status.HIDDEN
        self.moderated_by = moderator
        self.moderated_at = timezone.now()

    def reject(self, moderator):
        self.status = self.Status.REJECTED
        self.moderated_by = moderator
        self.moderated_at = timezone.now()


class ReviewReport(models.Model):
    class Reason(models.TextChoices):
        SPAM = 'spam', 'Spam'
        ABUSIVE_CONTENT = 'abusive_content', 'Abusive content'
        PERSONAL_DATA = 'personal_data', 'Personal data'
        FALSE_OR_MISLEADING = 'false_or_misleading', 'False or misleading'
        DUPLICATE = 'duplicate', 'Duplicate'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'
        REJECTED = 'rejected', 'Rejected'

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='review_reports')
    reason = models.CharField(max_length=40, choices=Reason.choices)
    details = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='resolved_review_reports',
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['review', 'reporter'], name='unique_report_per_review_reporter'),
        ]

    def __str__(self):
        return f'{self.get_reason_display()} report for {self.review}'

    def resolve(self, moderator):
        self.status = self.Status.RESOLVED
        self.resolved_by = moderator
        self.resolved_at = timezone.now()
