import uuid

from django.contrib.auth import get_user_model
from rest_framework import serializers

from households.models import Membership

from .models import Task

User = get_user_model()

STATUS_PENDING = 'pending'
STATUS_DONE = 'done'
ASSIGNEE_ME = 'me'


class TaskAssigneeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'name']


class TaskSerializer(serializers.ModelSerializer):
    # Ids de los responsables; assignee_details trae además el nombre
    assignees = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        many=True,
        error_messages={'does_not_exist': 'Each assignee must be an active member of this household.'},
    )
    assignee_details = TaskAssigneeSerializer(source='assignees', many=True, read_only=True)
    repeat_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
        max_length=7,
        help_text='Días en que se repite: 0 = lunes ... 6 = domingo. Vacío si no se repite.',
    )
    reminder_time = serializers.TimeField(
        required=False,
        allow_null=True,
        format='%H:%M',
        help_text='Hora local del recordatorio (HH:MM). null = sin recordatorio.',
    )
    is_done = serializers.BooleanField(read_only=True)
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'household', 'title', 'description', 'priority',
            'assignees', 'assignee_details', 'due_date', 'reminder_time', 'repeat_days',
            'is_done', 'completed_at', 'completed_by', 'next_occurrence',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['household', 'completed_at', 'completed_by', 'created_by', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Solo se puede asignar a miembros activos del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            self.fields['assignees'].child_relation.queryset = User.objects.filter(
                memberships__household=household,
                memberships__status=Membership.STATUS_ACTIVE,
            )

    def validate_assignees(self, value):
        if not value:
            raise serializers.ValidationError('Choose at least one assignee.')
        return value

    def validate_repeat_days(self, value):
        # Sin repetidos y en orden de la semana
        return sorted(set(value))

    def create(self, validated_data):
        assignees = validated_data.pop('assignees')
        task = super().create(validated_data)
        task.assignees.set(assignees)
        return task

    def update(self, instance, validated_data):
        assignees = validated_data.pop('assignees', None)
        task = super().update(instance, validated_data)
        if assignees is not None:
            task.assignees.set(assignees)
        return task

    def get_next_occurrence(self, obj) -> uuid.UUID | None:
        next_task = obj.get_next_occurrence()
        return next_task.pk if next_task else None


class CompleteTaskResponseSerializer(serializers.Serializer):
    task = TaskSerializer()
    # La siguiente de una tarea que se repite; null si no se repite
    next_task = TaskSerializer(allow_null=True)


class TaskFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[STATUS_PENDING, STATUS_DONE], required=False)
    assignee = serializers.CharField(required=False, help_text='"me" o el id de un miembro.')
    due_from = serializers.DateField(required=False, help_text='Vencen desde este día (AAAA-MM-DD).')
    due_to = serializers.DateField(required=False, help_text='Vencen hasta este día (AAAA-MM-DD).')

    def validate_assignee(self, value):
        if value == ASSIGNEE_ME:
            return value
        try:
            return uuid.UUID(value)
        except ValueError:
            raise serializers.ValidationError('Use "me" or a user id.')
