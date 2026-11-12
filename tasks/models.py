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

    # El MVP solo repite cada semana (mismo día y hora)
    REPEAT_NONE = 'none'
    REPEAT_WEEKLY = 'weekly'

    REPEAT_CHOICES = [
        (REPEAT_NONE, 'No repeat'),
        (REPEAT_WEEKLY, 'Weekly'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    # Asignación manual a un miembro del hogar; sin asignar si es null
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
    )
    # Fecha y hora obligatorias: la app programa el recordatorio local con ellas
    due_at = models.DateTimeField()
    repeat_rule = models.CharField(max_length=10, choices=REPEAT_CHOICES, default=REPEAT_NONE)
    # Tarea semanal de la que salió esta al completarla
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
        ordering = ['due_at', 'created_at']
        indexes = [models.Index(fields=['household', 'due_at'])]

    @property
    def is_done(self):
        return self.completed_at is not None

    def get_next_occurrence(self):
        try:
            return self.next_occurrence
        except Task.DoesNotExist:
            return None

    def create_next_occurrence(self):
        """Copia la tarea una semana después, pendiente y con la misma asignación."""
        return Task.objects.create(
            household=self.household,
            title=self.title,
            description=self.description,
            priority=self.priority,
            assignee=self.assignee,
            due_at=self.due_at + timedelta(weeks=1),
            repeat_rule=self.repeat_rule,
            previous=self,
            created_by=self.created_by,
        )

    def __str__(self):
        return self.title
