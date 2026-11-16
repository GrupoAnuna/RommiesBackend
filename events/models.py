import uuid

from django.conf import settings
from django.db import models

from households.models import Household

REPEAT_WEEKLY = 'weekly'
REPEAT_MONTHLY = 'monthly'
REPEAT_YEARLY = 'yearly'
REPEAT_CHOICES = [
    (REPEAT_WEEKLY, 'Cada semana'),
    (REPEAT_MONTHLY, 'Cada mes'),
    (REPEAT_YEARLY, 'Cada año'),
]

# Opciones de la hoja «Recordatorio», en minutos antes de empezar
REMINDER_CHOICES = [
    (0, 'A la hora del evento'),
    (10, '10 min antes'),
    (30, '30 min antes'),
    (60, '1 h antes'),
    (1440, '1 día antes'),
]


class Event(models.Model):
    """Evento del calendario del hogar.

    Si se repite, este registro es la serie: sus fechas se calculan con
    `occurrences.py` y no se guardan. Cambiar o borrar «solo este» día de una
    serie lo saca de la serie (`excluded_dates`) y, si se cambió, crea un evento
    suelto con `series` y `original_date` apuntando al día que reemplaza.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=120)
    notes = models.TextField(blank=True)
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='events', blank=True)
    # Día del evento (de la primera repetición si se repite), en la hora local del hogar
    date = models.DateField()
    all_day = models.BooleanField(default=False)
    # Hora local de inicio y fin; null si es de todo el día
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    # Repetición: vacío = no se repite
    repeat_frequency = models.CharField(max_length=10, choices=REPEAT_CHOICES, blank=True, default='')
    # Semanal: días de la semana, 0 = lunes ... 6 = domingo
    repeat_weekdays = models.JSONField(default=list, blank=True)
    # Último día en que puede repetirse; null = nunca termina
    repeat_until = models.DateField(null=True, blank=True)
    # Días (AAAA-MM-DD) que ya no son de la serie: borrados o cambiados «solo este»
    excluded_dates = models.JSONField(default=list, blank=True)
    # Evento suelto que reemplaza un día de una serie
    series = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='overrides',
    )
    original_date = models.DateField(null=True, blank=True)
    # Minutos antes de empezar en que avisa (notificación local); null = sin recordatorio
    reminder_minutes = models.PositiveIntegerField(choices=REMINDER_CHOICES, null=True, blank=True)
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
        ordering = ['date', 'start_time', 'created_at']
        indexes = [models.Index(fields=['household', 'date'], name='events_household_date_idx')]

    @property
    def is_recurring(self):
        return bool(self.repeat_frequency)

    def __str__(self):
        return self.title
