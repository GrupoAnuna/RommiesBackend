from django.contrib import admin

from .models import Expense, ExpenseParticipant, Payment


class ExpenseParticipantInline(admin.TabularInline):
    model = ExpenseParticipant
    extra = 0


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'amount', 'paid_by', 'spent_on']
    search_fields = ['title', 'household__name']
    inlines = [ExpenseParticipantInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['from_user', 'to_user', 'amount', 'household', 'paid_on']
    search_fields = ['household__name', 'note']
