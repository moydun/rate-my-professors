import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .caching import invalidate_catalog_cache
from .constants import MAX_COMMENT_LENGTH
from .sanitize import clean_text


class School(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, allow_unicode=True)
    city = models.CharField(max_length=80)

    class Meta:
        ordering = ["name"]
        indexes = [
            # `cities()` filters/distincts on this column for the home filters.
            models.Index(fields=["city"], name="school_city_idx"),
            # Default ordering and catalog lookups sort by name.
            models.Index(fields=["name"], name="school_name_idx"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True) or slugify(self.name)
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self):
        return self.name


class Professor(models.Model):
    STATUS_CHOICES = [("pending", "pending"), ("approved", "approved"), ("rejected", "rejected")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="professors")
    first_name = models.CharField(max_length=40)
    last_name = models.CharField(max_length=40)
    slug = models.SlugField(max_length=200, unique=True, allow_unicode=True)
    department = models.CharField(max_length=80)
    title = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="approved")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "first_name", "last_name"], name="uniq_prof_school_name"),
        ]
        indexes = [
            # `search()`/`departments()` filter the approved catalog.
            models.Index(fields=["status"], name="prof_status_idx"),
            # `professors_for_school()`/`school_stats()` scope by school first.
            models.Index(fields=["school", "status"], name="prof_school_status_idx"),
            # Department filter + values_list(distinct) on the home page.
            models.Index(fields=["department"], name="prof_dept_idx"),
            # Default ordering (`last_name`, `first_name`) sorts by name.
            models.Index(fields=["last_name", "first_name"], name="prof_name_idx"),
        ]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return f"{self.full_name} ({self.school})"

    def save(self, *args, **kwargs):
        if not self.slug:
            full = f"{self.first_name} {self.last_name}"
            base = slugify(full, allow_unicode=True) or slugify(full)
            slug = base
            i = 1
            while Professor.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Review(models.Model):
    STATUS_CHOICES = [("pending", "pending"), ("approved", "approved"), ("rejected", "rejected")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    professor = models.ForeignKey(Professor, on_delete=models.CASCADE, related_name="reviews")
    course = models.CharField(max_length=80)
    term = models.CharField(max_length=20, blank=True)
    overall = models.IntegerField()
    difficulty = models.IntegerField()
    would_take_again = models.BooleanField()
    grade = models.CharField(max_length=20, blank=True)
    comment = models.TextField(max_length=MAX_COMMENT_LENGTH)
    tags = models.ManyToManyField(Tag, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviews",
    )
    helpful = models.IntegerField(default=0)
    not_helpful = models.IntegerField(default=0)
    reported = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Один отзыв от одного пользователя на предмет у преподавателя.
            # Условие author__isnull=False оставляет анонимные/seed-отзывы без ограничения.
            models.UniqueConstraint(
                Lower("course"), F("professor"), F("author"),
                name="uniq_review_prof_author_course",
                condition=Q(author__isnull=False),
            ),
        ]
        indexes = [
            # `reviews_for()` and every stats aggregate filter by this pair.
            models.Index(fields=["professor", "status"], name="review_prof_status_idx"),
            # `all_reviews()` filters by status and the default order is -created_at.
            models.Index(fields=["status", "-created_at"], name="review_status_created_idx"),
        ]

    def clean(self):
        # Defence in depth for admin/ORM writers: drop control characters and
        # normalise line endings before validation.
        self.comment = clean_text(self.comment)

    def __str__(self):
        return f"{self.professor} {self.course} {self.overall}"


class Vote(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="votes")
    helpful = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "user"], name="uniq_vote_review_user"),
        ]


class Report(models.Model):
    REASON_SPAM = "spam"
    REASON_ABUSE = "abuse"
    REASON_PERSONAL_DATA = "personal_data"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_SPAM, _("Спам")),
        (REASON_ABUSE, _("Оскорбления или угрозы")),
        (REASON_PERSONAL_DATA, _("Личные данные")),
        (REASON_OTHER, _("Другое")),
    ]
    STATUS_PENDING = "pending"
    STATUS_REVIEWED = "reviewed"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Ожидает рассмотрения")),
        (STATUS_REVIEWED, _("Рассмотрена")),
    ]

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="reports")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_OTHER)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "user"], name="uniq_report_review_user"),
        ]


# Catalog pages cache derived review/professor/school data, so any ORM write
# that can change those pages invalidates the cache namespace. Bulk updates in
# repositories/admin call ``invalidate_catalog_cache`` explicitly because they
# do not emit model signals.
@receiver([post_save, post_delete], sender=Review)
@receiver([post_save, post_delete], sender=Professor)
@receiver([post_save, post_delete], sender=School)
def _invalidate_catalog_on_write(sender, **kwargs):
    invalidate_catalog_cache()
