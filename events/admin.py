from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'date', 'all_day', 'start_time', 'repeat_frequency', 'created_by']
    list_filter = ['repeat_frequency', 'all_day']
    search_fields = ['title', 'household__name']
    raw_id_fields = ['household', 'series', 'created_by']
    filter_horizontal = ['participants']
