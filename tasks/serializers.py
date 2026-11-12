import uuid

from django.contrib.auth import get_user_model
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from households.models import Membership

from .models import Task

User = get_user_model()

STATUS_PENDING = 'pending'
STATUS_DONE = 'done'
ASSIGNEE_ME = 'me'
ASSIGNEE_UNASSIGNED = 'unassigned'


class DueAtField(serializers.DateTimeField):
    """Fecha y hora con zona horaria: «2026-11-20T18:30:00-06:00».

    Sin hora o sin zona se rechaza: el recordatorio sonaría a otra hora.
    """

    default_error_messages = {
        'incomplete': 'Include date, time and UTC offset, e.g. 2026-11-20T18:30:00-06:00.',
    }

    def to_internal_value(self, value):
        if isinstance(value, str):
            parsed = parse_datetime(value.strip())
            if parsed is None or parsed.tzinfo is None:
                self.fail('incomplete')
        return super().to_internal_value(value)


class TaskSerializer(serializers.ModelSerializer):
    assignee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        allow_null=True,
        required=False,
        error_messages={'does_not_exist': 'The assignee must be an active member of this household.'},
    )
    assignee_name = serializers.CharField(source='assignee.name', read_only=True, default=None)
    due_at = DueAtField()
    is_done = serializers.BooleanField(read_only=True)
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'household', 'title', 'description', 'priority',
            'assignee', 'assignee_name', 'due_at', 'repeat_rule',
            'is_done', 'completed_at', 'completed_by', 'next_occurrence',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['household', 'completed_at', 'completed_by', 'created_by', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Solo se puede asignar a miembros activos del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            self.fields['assignee'].queryset = User.objects.filter(
                memberships__household=household,
                memberships__status=Membership.STATUS_ACTIVE,
            )

    def get_next_occurrence(self, obj) -> uuid.UUID | None:
        next_task = obj.get_next_occurrence()
        return next_task.pk if next_task else None


class CompleteTaskResponseSerializer(serializers.Serializer):
    task = TaskSerializer()
    # La siguiente semana de una tarea semanal; null si no se repite
    next_task = TaskSerializer(allow_null=True)


class TaskFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[STATUS_PENDING, STATUS_DONE], required=False)
    assignee = serializers.CharField(
        required=False,
        help_text='"me", "unassigned" o el id de un miembro.',
    )
    due_from = serializers.DateTimeField(required=False, help_text='Desde esta fecha y hora (ISO 8601).')
    due_to = serializers.DateTimeField(required=False, help_text='Hasta esta fecha y hora (ISO 8601).')

    def validate_assignee(self, value):
        if value in (ASSIGNEE_ME, ASSIGNEE_UNASSIGNED):
            return value
        try:
            return uuid.UUID(value)
        except ValueError:
            raise serializers.ValidationError('Use "me", "unassigned" or a user id.')
