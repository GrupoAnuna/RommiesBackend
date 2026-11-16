"""Agenda del calendario: eventos, tareas pendientes y gastos que se repiten, día por día.

- Eventos: cada repetición que cae en el rango (ver occurrences.py).
- Tareas: las pendientes que vencen en el rango y, si se repiten y todavía no se
  creó la siguiente, los próximos días marcados (aún no existen como registros).
- Gastos: los de una serie que se repite, ya creados o programados en el rango.

Orden de cada día: primero lo que no tiene hora (eventos de todo el día, tareas y
gastos) y después los eventos por hora de inicio.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q

from expenses.models import Expense, RecurringExpense
from expenses.recurrence import due_dates, generate_due
from households.models import Membership
from tasks.models import Task

from .models import Event
from .occurrences import occurrences
from .serializers import ITEM_EVENT, ITEM_EXPENSE, ITEM_TASK

User = get_user_model()

UNTIMED_ORDER = {ITEM_EVENT: 0, ITEM_TASK: 1, ITEM_EXPENSE: 2}


def person(user):
    return {'id': user.pk, 'name': user.name}


def item(kind, pk, day, title, people, member_ids, *, all_day=True, start=None, end=None,
         recurring=False, amount=None, expense_id=None, scheduled=False, reminder_minutes=None):
    people = list(people)
    return {
        'type': kind,
        'id': pk,
        'date': day,
        'title': title,
        'all_day': all_day,
        'start_time': start,
        'end_time': end,
        'recurring': recurring,
        'people': [person(user) for user in people],
        'everyone': bool(member_ids) and member_ids <= {user.pk for user in people},
        'amount': amount,
        'expense_id': expense_id,
        'scheduled': scheduled,
        'reminder_minutes': reminder_minutes,
    }


def event_items(household, start, end, member_ids):
    events = (
        Event.objects.filter(household=household, date__lte=end)
        .filter(
            Q(repeat_frequency='', date__gte=start)
            | (~Q(repeat_frequency='') & (Q(repeat_until__isnull=True) | Q(repeat_until__gte=start)))
        )
        .prefetch_related('participants')
    )
    for event in events:
        participants = list(event.participants.all())
        for day in occurrences(event, start, end):
            yield item(
                ITEM_EVENT, event.pk, day, event.title, participants, member_ids,
                all_day=event.all_day, start=event.start_time, end=event.end_time,
                recurring=event.is_recurring or event.series_id is not None,
                reminder_minutes=event.reminder_minutes,
            )


def task_items(household, start, end, member_ids):
    tasks = (
        Task.objects.filter(household=household, completed_at__isnull=True, due_date__lte=end)
        .select_related('next_occurrence')
        .prefetch_related('assignees')
    )
    for task in tasks:
        assignees = list(task.assignees.all())
        recurring = bool(task.repeat_days)
        if task.due_date >= start:
            yield item(ITEM_TASK, task.pk, task.due_date, task.title, assignees, member_ids, recurring=recurring)
        if not recurring or task.get_next_occurrence() is not None:
            continue
        # Próximos días marcados de una tarea que se repite
        day = max(task.due_date + timedelta(days=1), start)
        while day <= end:
            if day.weekday() in task.repeat_days:
                yield item(ITEM_TASK, task.pk, day, task.title, assignees, member_ids, recurring=True, scheduled=True)
            day += timedelta(days=1)


def expense_items(household, start, end, member_ids):
    generate_due(household)

    # Ya creados en el rango
    created = (
        Expense.objects.filter(household=household, series__isnull=False, spent_on__range=(start, end))
        .prefetch_related('payers__user')
    )
    for expense in created:
        yield item(
            ITEM_EXPENSE, expense.series_id, expense.spent_on, expense.title,
            [payer.user for payer in expense.payers.all()], member_ids,
            recurring=True, amount=expense.amount, expense_id=expense.pk,
        )

    # Programados: todavía no existen
    series_list = RecurringExpense.objects.filter(household=household, active=True, last_date__lt=end)
    users = {}
    for series in series_list:
        days = [day for day in due_dates(series, end) if day >= start]
        if not days:
            continue
        template = series.template
        payer_ids = [payer['user'] for payer in template.get('payers', [])]
        missing = [pk for pk in payer_ids if pk not in users]
        if missing:
            users.update({str(user.pk): user for user in User.objects.filter(pk__in=missing)})
        payers = [users[pk] for pk in payer_ids if pk in users]
        latest = series.occurrences.order_by('-spent_on').values_list('pk', flat=True).first()
        for day in days:
            yield item(
                ITEM_EXPENSE, series.pk, day, template.get('title', ''), payers, member_ids,
                recurring=True, amount=Decimal(template.get('amount', '0')), expense_id=latest, scheduled=True,
            )


def sort_key(entry):
    timed = not entry['all_day'] and entry['start_time'] is not None
    return (
        entry['date'],
        timed,
        entry['start_time'] if timed else UNTIMED_ORDER[entry['type']],
        entry['title'].lower(),
    )


def build_agenda(household, start, end):
    member_ids = set(
        User.objects.filter(
            memberships__household=household,
            memberships__status=Membership.STATUS_ACTIVE,
        ).values_list('pk', flat=True)
    )
    items = [
        *event_items(household, start, end, member_ids),
        *task_items(household, start, end, member_ids),
        *expense_items(household, start, end, member_ids),
    ]
    items.sort(key=sort_key)
    return {
        'date_from': start,
        'date_to': end,
        'busy_days': sorted({entry['date'] for entry in items}),
        'items': items,
    }
