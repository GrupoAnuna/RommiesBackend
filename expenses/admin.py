from django.contrib import admin

from .models import Expense, ExpenseParticipant


class ExpenseParticipantInline(admin.TabularInline):
    model = ExpenseParticipant
    extra = 0


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'amount', 'paid_by', 'spent_on', 'settled_at']
    search_fields = ['title', 'household__name']
    inlines = [ExpenseParticipantInline]
