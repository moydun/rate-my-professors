from django.db import models

from universities.models import Department, University


class ProfessorQuerySet(models.QuerySet):
    def public(self):
        return self.filter(
            is_active=True,
            departments__is_active=True,
            departments__university__is_active=True,
        ).distinct()


class CourseQuerySet(models.QuerySet):
    def public(self):
        return self.filter(
            is_active=True,
            university__is_active=True,
            department__is_active=True,
            department__university__is_active=True,
        )


class Professor(models.Model):
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120)
    middle_name = models.CharField(max_length=120, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=255, unique=True)
    departments = models.ManyToManyField(Department, related_name='professors')
    bio = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProfessorQuerySet.as_manager()

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        if self.display_name:
            return self.display_name

        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(part for part in parts if part)

    def rating_summary(self):
        from reviews.models import Review

        return Review.objects.for_professor(self).published_summary()


class Course(models.Model):
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='courses')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses')
    professors = models.ManyToManyField(Professor, related_name='courses', blank=True)
    code = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CourseQuerySet.as_manager()

    class Meta:
        ordering = ['code', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['university', 'code'],
                condition=~models.Q(code=''),
                name='unique_course_code_per_university',
            ),
        ]

    def __str__(self):
        if self.code:
            return f'{self.code} - {self.name}'
        return self.name
