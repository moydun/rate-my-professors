from django.db import models


class UniversityQuerySet(models.QuerySet):
    def public(self):
        return self.filter(is_active=True)


class University(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    city = models.CharField(max_length=120)
    website_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UniversityQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['name', 'city'], name='unique_university_name_city'),
        ]

    def __str__(self):
        return self.name


class DepartmentQuerySet(models.QuerySet):
    def public(self):
        return self.filter(is_active=True, university__is_active=True)


class Department(models.Model):
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DepartmentQuerySet.as_manager()

    class Meta:
        ordering = ['university__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['university', 'slug'], name='unique_department_slug_per_university'),
            models.UniqueConstraint(fields=['university', 'name'], name='unique_department_name_per_university'),
        ]

    def __str__(self):
        return f'{self.name} - {self.university}'
