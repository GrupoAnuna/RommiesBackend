import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from households.models import Household

# Montos siempre en Decimal (nunca float): hasta 9 999 999 999.99
AMOUNT_DIGITS = 12
AMOUNT_DECIMALS = 2
MIN_AMOUNT = MinValueValidator(Decimal('0.01'))


def amount_field(**kwargs):
    return models.DecimalField(
        max_digits=AMOUNT_DIGITS,
        decimal_places=AMOUNT_DECIMALS,
        validators=[MIN_AMOUNT],
        **kwargs,
    )


class Expense(models.Model):
    """Quién pagó qué y lo que le toca a cada uno. Los saldos se calculan en balances.py."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=120)
    amount = amount_field()
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='paid_expenses')
    # Día del gasto (no el de captura)
    spent_on = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-spent_on', '-created_at']
        indexes = [models.Index(fields=['household', 'spent_on'], name='expenses_household_date_idx')]

    def __str__(self):
        return f'{self.title} ({self.amount})'


class ExpenseParticipant(models.Model):
    """Lo que le toca pagar a una persona de un gasto."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expense_shares')
    amount = amount_field()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['expense', 'user'], name='unique_expense_participant'),
        ]

    def __str__(self):
        return f'{self.user} · {self.amount}'


class Payment(models.Model):
    """Reembolso: dinero que una persona le pasó a otra para saldar cuentas."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='payments')
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payments_sent')
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payments_received')
    amount = amount_field()
    paid_on = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-created_at']
        indexes = [models.Index(fields=['household', 'paid_on'], name='payments_household_date_idx')]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(from_user=models.F('to_user')),
                name='payment_between_different_people',
            ),
        ]

    def __str__(self):
        return f'{self.from_user} → {self.to_user} · {self.amount}'
