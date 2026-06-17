from django.contrib import admin

from .models import Department, University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'is_active', 'updated_at']
    list_filter = ['is_active', 'city']
    prepopulated_fields = {'slug': ['name']}
    search_fields = ['name', 'city']


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'university', 'is_active', 'updated_at']
    list_filter = ['is_active', 'university']
    prepopulated_fields = {'slug': ['name']}
    search_fields = ['name', 'university__name']
