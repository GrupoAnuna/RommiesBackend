from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'due_date', 'reminder_time', 'completed_at']
    list_filter = ['priority']
    search_fields = ['title', 'household__name']
