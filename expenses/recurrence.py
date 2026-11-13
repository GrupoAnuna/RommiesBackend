"""Gastos que se repiten.

Cada repetición se crea el día que toca, copiando la plantilla de la serie
(título, monto, categoría, reparto, quién pagó). Se generan:

- al abrir los gastos, los saldos o la actividad del hogar (así funciona sin cron), y
- con `python manage.py generate_recurring_expenses`, para programarlo una vez al día.

Generar dos veces no duplica: hay un gasto por serie y día (`unique_series_occurrence`).
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import REPEAT_MONTHLY, REPEAT_WEEKLY, Expense, ExpenseParticipant, ExpensePayer, RecurringExpense


def month_day(year, month, day):
    """El día `day` de ese mes; si el mes es más corto, el último (31 → 30 de abril)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def due_dates(series, until):
    """Días después de `series.last_date` y hasta `until` (y `ends_on`) en que toca un gasto."""
    last = series.ends_on if series.ends_on and series.ends_on < until else until
    day = series.last_date
    if series.frequency == REPEAT_WEEKLY:
        weekdays = set(series.weekdays)
        day += timedelta(days=1)
        while day <= last:
            if day.weekday() in weekdays:
                yield day
            day += timedelta(days=1)
    elif series.frequency == REPEAT_MONTHLY:
        year, month = day.year, day.month
        while True:
            candidate = month_day(year, month, series.day_of_month)
            if candidate > last:
                return
            if candidate > series.last_date:
                yield candidate
            month += 1
            if month > 12:
                year, month = year + 1, 1


def template_of(expense):
    """Plantilla de la serie a partir de un gasto (sus pagadores y participantes ya guardados)."""
    return {
        'title': expense.title,
        'amount': str(expense.amount),
        'category': expense.category,
        'split_mode': expense.split_mode,
        'created_by': str(expense.created_by_id) if expense.created_by_id else None,
        'payers': [{'user': str(p.user_id), 'amount': str(p.amount)} for p in expense.payers.all()],
        'participants': [{'user': str(p.user_id), 'amount': str(p.amount)} for p in expense.participants.all()],
    }


def create_occurrence(series, day):
    template = series.template
    try:
        with transaction.atomic():
            expense = Expense.objects.create(
                household_id=series.household_id,
                title=template['title'],
                amount=Decimal(template['amount']),
                category=template.get('category', ''),
                split_mode=template.get('split_mode', ''),
                spent_on=day,
                series=series,
                created_by_id=template.get('created_by'),
            )
            ExpensePayer.objects.bulk_create(
                ExpensePayer(expense=expense, user_id=p['user'], amount=Decimal(p['amount']))
                for p in template['payers']
            )
            ExpenseParticipant.objects.bulk_create(
                ExpenseParticipant(expense=expense, user_id=p['user'], amount=Decimal(p['amount']))
                for p in template['participants']
            )
    except IntegrityError:
        # Otra petición ya creó la de este día
        return None
    return expense


def generate_due(household=None, today=None):
    """Crea las repeticiones pendientes hasta hoy. Devuelve cuántas creó."""
    today = today or timezone.localdate()
    series_list = RecurringExpense.objects.filter(active=True, last_date__lt=today)
    if household is not None:
        series_list = series_list.filter(household=household)

    created = 0
    for series in series_list:
        last = series.last_date
        for day in due_dates(series, today):
            if create_occurrence(series, day) is not None:
                created += 1
            last = day
        # Se avanza aunque no tocara ningún día (p. ej. semanas sin días marcados)
        series.last_date = max(last, min(today, series.ends_on or today))
        if series.ends_on and series.last_date >= series.ends_on:
            series.active = False
        series.save(update_fields=['last_date', 'active'])
    return created
