from django.contrib import admin

from .models import Review, ReviewReport


@admin.action(description='Publish selected reviews')
def publish_reviews(modeladmin, request, queryset):
    for review in queryset:
        review.publish(request.user)
        review.save(update_fields=['status', 'moderated_by', 'moderated_at', 'published_at', 'updated_at'])


@admin.action(description='Hide selected reviews')
def hide_reviews(modeladmin, request, queryset):
    for review in queryset:
        review.hide(request.user)
        review.save(update_fields=['status', 'moderated_by', 'moderated_at', 'updated_at'])


@admin.action(description='Reject selected reviews')
def reject_reviews(modeladmin, request, queryset):
    for review in queryset:
        review.reject(request.user)
        review.save(update_fields=['status', 'moderated_by', 'moderated_at', 'updated_at'])


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    actions = [publish_reviews, hide_reviews, reject_reviews]
    list_display = ['professor', 'course', 'author', 'rating', 'difficulty', 'status', 'created_at']
    list_filter = ['status', 'rating', 'difficulty', 'would_take_again', 'created_at']
    search_fields = ['professor__first_name', 'professor__last_name', 'course__code', 'course__name', 'author__username', 'comment']
    readonly_fields = ['created_at', 'updated_at', 'published_at', 'moderated_at']


@admin.action(description='Resolve selected reports')
def resolve_reports(modeladmin, request, queryset):
    for report in queryset:
        report.resolve(request.user)
        report.save(update_fields=['status', 'resolved_by', 'resolved_at'])


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    actions = [resolve_reports]
    list_display = ['review', 'reporter', 'reason', 'status', 'created_at']
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['review__professor__first_name', 'review__professor__last_name', 'reporter__username', 'details']
    readonly_fields = ['created_at', 'resolved_at']
