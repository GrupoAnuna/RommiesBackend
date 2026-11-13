import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models

from households.models import Household


class Task(models.Model):
    PRIORITY_LOW = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH = 'high'

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH, 'High'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    # Responsables: al menos uno, todos miembros activos del hogar
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='assigned_tasks', blank=True)
    # Día en que vence, en la hora local del hogar
    due_date = models.DateField()
    # Hora del recordatorio local en el teléfono de cada responsable; null = sin aviso
    reminder_time = models.TimeField(null=True, blank=True)
    # Días en que se repite, 0 = lunes ... 6 = domingo; vacío si no se repite
    repeat_days = models.JSONField(default=list, blank=True)
    # Tarea de la que salió esta al completarla
    previous = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_occurrence',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
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
        ordering = ['due_date', 'reminder_time', 'created_at']
        indexes = [models.Index(fields=['household', 'due_date'], name='tasks_household_due_date_idx')]

    @property
    def is_done(self):
        return self.completed_at is not None

    def next_due_date(self):
        """El siguiente día marcado después de la fecha límite actual."""
        for offset in range(1, 8):
            day = self.due_date + timedelta(days=offset)
            if day.weekday() in self.repeat_days:
                return day
        return None

    def get_next_occurrence(self):
        try:
            return self.next_occurrence
        except Task.DoesNotExist:
            return None

    def create_next_occurrence(self):
        """Copia la tarea al siguiente día marcado, pendiente y con los mismos responsables."""
        next_task = Task.objects.create(
            household=self.household,
            title=self.title,
            description=self.description,
            priority=self.priority,
            due_date=self.next_due_date(),
            reminder_time=self.reminder_time,
            repeat_days=self.repeat_days,
            previous=self,
            created_by=self.created_by,
        )
        next_task.assignees.set(self.assignees.all())
        return next_task

    def __str__(self):
        return self.title
