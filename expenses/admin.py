from django.contrib import admin

from .models import Expense, ExpenseParticipant, ExpensePayer, Payment, Receipt, RecurringExpense


class ExpensePayerInline(admin.TabularInline):
    model = ExpensePayer
    extra = 0


class ExpenseParticipantInline(admin.TabularInline):
    model = ExpenseParticipant
    extra = 0


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['title', 'household', 'amount', 'category', 'spent_on', 'series']
    search_fields = ['title', 'household__name']
    inlines = [ExpensePayerInline, ExpenseParticipantInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['from_user', 'to_user', 'amount', 'household', 'paid_on', 'expense']
    search_fields = ['household__name', 'note']


@admin.register(RecurringExpense)
class RecurringExpenseAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'household', 'frequency', 'starts_on', 'ends_on', 'last_date', 'active']


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ['image', 'household', 'uploaded_by', 'created_at']
