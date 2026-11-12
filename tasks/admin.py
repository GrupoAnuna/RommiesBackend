from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'assignee', 'due_at', 'repeat_rule', 'completed_at']
    list_filter = ['repeat_rule', 'priority']
    search_fields = ['title', 'household__name']
