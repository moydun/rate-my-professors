from django.contrib import admin
from django.db.models import DateTimeField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Review, ReviewReport


@admin.action(description='Publish selected reviews')
def publish_reviews(modeladmin, request, queryset):
    now = timezone.now()
    queryset.update(
        status=Review.Status.PUBLISHED,
        moderated_by_id=request.user.pk,
        moderated_at=now,
        published_at=Coalesce('published_at', Value(now), output_field=DateTimeField()),
        updated_at=now,
    )


@admin.action(description='Hide selected reviews')
def hide_reviews(modeladmin, request, queryset):
    now = timezone.now()
    queryset.update(
        status=Review.Status.HIDDEN,
        moderated_by_id=request.user.pk,
        moderated_at=now,
        updated_at=now,
    )


@admin.action(description='Reject selected reviews')
def reject_reviews(modeladmin, request, queryset):
    now = timezone.now()
    queryset.update(
        status=Review.Status.REJECTED,
        moderated_by_id=request.user.pk,
        moderated_at=now,
        updated_at=now,
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    actions = [publish_reviews, hide_reviews, reject_reviews]
    list_display = ['professor', 'course', 'author', 'rating', 'difficulty', 'status', 'created_at']
    list_filter = ['status', 'rating', 'difficulty', 'would_take_again', 'created_at']
    search_fields = ['professor__first_name', 'professor__last_name', 'course__code', 'course__name', 'author__username', 'comment']
    readonly_fields = ['created_at', 'updated_at', 'published_at', 'moderated_at']


@admin.action(description='Resolve selected reports')
def resolve_reports(modeladmin, request, queryset):
    now = timezone.now()
    queryset.update(
        status=ReviewReport.Status.RESOLVED,
        resolved_by_id=request.user.pk,
        resolved_at=now,
    )


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    actions = [resolve_reports]
    list_display = ['review', 'reporter', 'reason', 'status', 'created_at']
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['review__professor__first_name', 'review__professor__last_name', 'reporter__username', 'details']
    readonly_fields = ['created_at', 'resolved_at']
