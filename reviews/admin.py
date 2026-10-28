from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .caching import invalidate_catalog_cache
from .models import Professor, Report, Review, School, Tag, Vote


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "slug"]
    list_filter = ["city"]
    search_fields = ["name", "city"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name"]


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    fields = ["course", "overall", "difficulty", "status", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Professor)
class ProfessorAdmin(admin.ModelAdmin):
    list_display = ["full_name", "school", "department", "title", "status", "created_at"]
    list_select_related = ["school"]
    list_filter = ["status", "school", "department"]
    search_fields = ["first_name", "last_name", "department"]
    actions = ["approve_selected"]

    @admin.action(description=_("Одобрить выбранных преподавателей"))
    def approve_selected(self, request, queryset):
        queryset.update(status="approved")
        invalidate_catalog_cache()

    def full_name(self, obj):
        return obj.full_name
    full_name.short_description = _("ФИО")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["professor", "course", "overall", "difficulty", "grade", "status", "reported", "created_at"]
    list_select_related = ["professor__school"]
    list_filter = ["status", "overall", "grade"]
    search_fields = ["comment", "course", "professor__first_name", "professor__last_name"]
    actions = ["approve_selected", "reject_selected"]
    filter_horizontal = ["tags"]

    @admin.action(description=_("Одобрить выбранные отзывы"))
    def approve_selected(self, request, queryset):
        queryset.update(status="approved")
        invalidate_catalog_cache()

    @admin.action(description=_("Отклонить выбранные отзывы"))
    def reject_selected(self, request, queryset):
        queryset.update(status="rejected")
        invalidate_catalog_cache()


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ["review", "user", "helpful", "created_at"]
    list_select_related = ["review__professor__school", "user"]
    list_filter = ["helpful"]


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["review", "user", "reason", "created_at", "status"]
    list_select_related = ["review__professor__school", "user"]
    list_filter = ["status"]
    search_fields = ["reason", "review__course", "user__username"]
    actions = ["mark_reviewed"]

    @admin.action(description=_("Отметить выбранные жалобы рассмотренными"))
    def mark_reviewed(self, request, queryset):
        queryset.update(status=Report.STATUS_REVIEWED)
