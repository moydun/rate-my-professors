from django.contrib import admin

from .models import Course, Professor


@admin.register(Professor)
class ProfessorAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'is_active', 'updated_at']
    list_filter = ['is_active', 'departments__university', 'departments']
    prepopulated_fields = {'slug': ['first_name', 'last_name']}
    search_fields = ['first_name', 'middle_name', 'last_name', 'display_name', 'departments__name']


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'university', 'department', 'is_active']
    list_filter = ['is_active', 'university', 'department']
    search_fields = ['code', 'name', 'university__name', 'department__name']
