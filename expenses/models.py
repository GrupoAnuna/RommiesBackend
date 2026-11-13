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

# Categorías del diseño (hoja «Categoría»); vacía = «Sin categoría»
CATEGORY_CHOICES = [
    ('supermercado', 'Supermercado'),
    ('servicios', 'Servicios'),
    ('alquiler', 'Alquiler'),
    ('edificio', 'Gastos del edificio'),
    ('limpieza', 'Limpieza'),
    ('comida', 'Comida'),
    ('transporte', 'Transporte'),
    ('otros', 'Otros'),
]

SPLIT_EQUAL = 'equal'
SPLIT_AMOUNTS = 'amounts'
SPLIT_CHOICES = [(SPLIT_EQUAL, 'Partes iguales'), (SPLIT_AMOUNTS, 'Montos')]

REPEAT_WEEKLY = 'weekly'
REPEAT_MONTHLY = 'monthly'
REPEAT_CHOICES = [(REPEAT_WEEKLY, 'Cada semana'), (REPEAT_MONTHLY, 'Cada mes')]


def amount_field(**kwargs):
    return models.DecimalField(
        max_digits=AMOUNT_DIGITS,
        decimal_places=AMOUNT_DECIMALS,
        validators=[MIN_AMOUNT],
        **kwargs,
    )


def receipt_path(receipt, filename):
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'jpg'
    return f'receipts/{receipt.household_id}/{receipt.id}.{extension}'


class Receipt(models.Model):
    """Foto del ticket. Se sube antes de guardar el gasto y el gasto la referencia."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='receipts')
    image = models.ImageField(upload_to=receipt_path)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.image.name


class RecurringExpense(models.Model):
    """Serie de un gasto que se repite. Cada repetición es un Expense que se crea
    el día que toca (ver recurrence.py) copiando `template`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='recurring_expenses')
    frequency = models.CharField(max_length=10, choices=REPEAT_CHOICES)
    # Semanal: días de la semana (0 = lunes). Mensual: el día del mes de starts_on
    weekdays = models.JSONField(default=list, blank=True)
    day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    # Último día que ya tiene su gasto creado
    last_date = models.DateField()
    active = models.BooleanField(default=True)
    # Título, monto, categoría, reparto, pagadores y participantes de cada repetición
    template = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.template.get("title", "")} ({self.get_frequency_display()})'


class Expense(models.Model):
    """Quién pagó qué y lo que le toca a cada uno. Los saldos se calculan en balances.py."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=120)
    amount = amount_field()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True, default='')
    split_mode = models.CharField(max_length=10, choices=SPLIT_CHOICES, default=SPLIT_AMOUNTS)
    # Día del gasto (no el de captura)
    spent_on = models.DateField(default=timezone.localdate)
    receipt = models.ForeignKey(Receipt, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    series = models.ForeignKey(
        RecurringExpense,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='occurrences',
    )
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
        constraints = [
            # Una repetición por día: generar dos veces no duplica
            models.UniqueConstraint(fields=['series', 'spent_on'], name='unique_series_occurrence'),
        ]

    @property
    def repeat(self):
        series = self.series
        if series is None or not series.active:
            return None
        return {'frequency': series.frequency, 'weekdays': series.weekdays, 'ends_on': series.ends_on}

    def __str__(self):
        return f'{self.title} ({self.amount})'


class ExpensePayer(models.Model):
    """Lo que puso una persona para pagar un gasto (puede haber varias)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='payers')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expense_payments')
    amount = amount_field()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['expense', 'user'], name='unique_expense_payer'),
        ]

    def __str__(self):
        return f'{self.user} pagó {self.amount}'


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
    # «Saldar tu parte» de un gasto: el pago queda ligado a él. Si se borra el
    # gasto, el pago sigue (el dinero ya se movió)
    expense = models.ForeignKey(Expense, on_delete=models.SET_NULL, null=True, blank=True, related_name='settlements')
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
