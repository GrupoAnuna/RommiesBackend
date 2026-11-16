"""Fechas en que cae un evento que se repite.

Las repeticiones no se guardan: se calculan desde la regla (frecuencia, días,
hasta cuándo) y se saltan los días de `excluded_dates`.
"""
import calendar
from datetime import date, timedelta

from .models import REPEAT_MONTHLY, REPEAT_WEEKLY, REPEAT_YEARLY


def clamp_day(year, month, day):
    """El día `day` de ese mes; si el mes es más corto, el último (31 → 30 de abril, 29 feb → 28)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def weekdays_of(event):
    # Semanal sin días marcados: el mismo día de la semana que la fecha del evento
    return set(event.repeat_weekdays) or {event.date.weekday()}


def occurrences(event, start, end):
    """Días entre `start` y `end` (incluidos) en que cae el evento, en orden."""
    first = event.date
    if not event.repeat_frequency:
        if start <= first <= end:
            yield first
        return

    last = min(end, event.repeat_until) if event.repeat_until else end
    begin = max(start, first)
    if begin > last:
        return
    excluded = set(event.excluded_dates)

    for day in _rule_dates(event, begin, last):
        if day.isoformat() not in excluded:
            yield day


def _rule_dates(event, begin, last):
    first = event.date
    if event.repeat_frequency == REPEAT_WEEKLY:
        weekdays = weekdays_of(event)
        day = begin
        while day <= last:
            if day.weekday() in weekdays:
                yield day
            day += timedelta(days=1)
    elif event.repeat_frequency == REPEAT_MONTHLY:
        year, month = begin.year, begin.month
        while True:
            day = clamp_day(year, month, first.day)
            if day > last:
                return
            if day >= begin:
                yield day
            month += 1
            if month > 12:
                year, month = year + 1, 1
    elif event.repeat_frequency == REPEAT_YEARLY:
        for year in range(begin.year, last.year + 1):
            day = clamp_day(year, first.month, first.day)
            if begin <= day <= last:
                yield day


def is_occurrence(event, day):
    return next(occurrences(event, day, day), None) == day
