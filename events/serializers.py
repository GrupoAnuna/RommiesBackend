from datetime import timedelta

from django.contrib.auth import get_user_model
from rest_framework import serializers

from households.models import Membership

from .models import REMINDER_CHOICES, REPEAT_CHOICES, REPEAT_WEEKLY, Event

User = get_user_model()

SCOPE_ALL = 'all'
SCOPE_THIS = 'this'
SCOPE_FOLLOWING = 'following'

NOTES_MAX_LENGTH = 1000
# Rango máximo de la agenda: un mes con las semanas de los bordes sobra
CALENDAR_MAX_DAYS = 93

ITEM_EVENT = 'event'
ITEM_TASK = 'task'
ITEM_EXPENSE = 'expense'


def display_name(user):
    return user.name or user.email


class PersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'name']


class EventSerializer(serializers.ModelSerializer):
    # Ids de los participantes; participant_details trae además el nombre
    participants = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        many=True,
        error_messages={'does_not_exist': 'Each participant must be an active member of this household.'},
    )
    participant_details = PersonSerializer(source='participants', many=True, read_only=True)
    everyone = serializers.SerializerMethodField(help_text='Participan todos los miembros activos del hogar.')
    notes = serializers.CharField(required=False, allow_blank=True, max_length=NOTES_MAX_LENGTH)
    start_time = serializers.TimeField(
        required=False,
        allow_null=True,
        format='%H:%M',
        help_text='Hora local de inicio (HH:MM). Obligatoria si no es de todo el día.',
    )
    end_time = serializers.TimeField(
        required=False,
        allow_null=True,
        format='%H:%M',
        help_text='Hora local de fin (HH:MM), posterior al inicio. Obligatoria si no es de todo el día.',
    )
    repeat_frequency = serializers.ChoiceField(
        choices=REPEAT_CHOICES,
        required=False,
        allow_blank=True,
        help_text='Vacío = no se repite. Mensual repite el mismo día del mes; anual, la misma fecha.',
    )
    repeat_weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
        max_length=7,
        help_text='Solo semanal: días en que se repite, 0 = lunes ... 6 = domingo.',
    )
    repeat_until = serializers.DateField(
        required=False,
        allow_null=True,
        help_text='Último día en que se repite (posterior a la fecha). null = nunca termina.',
    )
    reminder_minutes = serializers.ChoiceField(
        choices=REMINDER_CHOICES,
        required=False,
        allow_null=True,
        help_text='Minutos antes de empezar en que avisa: 0, 10, 30, 60 o 1440. null = sin recordatorio.',
    )
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'household', 'title', 'notes', 'participants', 'participant_details', 'everyone',
            'date', 'all_day', 'start_time', 'end_time',
            'repeat_frequency', 'repeat_weekdays', 'repeat_until', 'excluded_dates',
            'series', 'original_date', 'reminder_minutes',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'household', 'excluded_dates', 'series', 'original_date',
            'created_by', 'created_at', 'updated_at',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Solo pueden participar miembros activos del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            self.fields['participants'].child_relation.queryset = self._members(household)

    @staticmethod
    def _members(household):
        return User.objects.filter(
            memberships__household=household,
            memberships__status=Membership.STATUS_ACTIVE,
        )

    def validate_participants(self, value):
        if not value:
            raise serializers.ValidationError('Choose at least one participant.')
        return value

    def validate_repeat_weekdays(self, value):
        # Sin repetidos y en orden de la semana
        return sorted(set(value))

    def validate(self, attrs):
        current = self.instance

        def value(name, default=None):
            if name in attrs:
                return attrs[name]
            return getattr(current, name) if current is not None else default

        all_day = value('all_day', False)
        if all_day:
            attrs['start_time'] = None
            attrs['end_time'] = None
        else:
            start, end = value('start_time'), value('end_time')
            errors = {}
            if start is None:
                errors['start_time'] = 'Choose when the event starts.'
            if end is None:
                errors['end_time'] = 'Choose when the event ends.'
            elif start is not None and end <= start:
                errors['end_time'] = 'The event must end after it starts.'
            if errors:
                raise serializers.ValidationError(errors)

        frequency = value('repeat_frequency', '')
        if not frequency:
            attrs['repeat_weekdays'] = []
            attrs['repeat_until'] = None
        else:
            if frequency != REPEAT_WEEKLY:
                attrs['repeat_weekdays'] = []
            elif not value('repeat_weekdays', []):
                # Semanal sin días: el día de la semana de la fecha
                attrs['repeat_weekdays'] = [value('date').weekday()]
            until = value('repeat_until')
            if until is not None and until <= value('date'):
                raise serializers.ValidationError({'repeat_until': 'The repeat must end after the event date.'})
        return attrs

    def create(self, validated_data):
        participants = validated_data.pop('participants')
        event = super().create(validated_data)
        event.participants.set(participants)
        return event

    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        event = super().update(instance, validated_data)
        if participants is not None:
            event.participants.set(participants)
        return event

    def get_everyone(self, obj) -> bool:
        household = self.context.get('household')
        if household is None:
            return False
        member_ids = set(self._members(household).values_list('pk', flat=True))
        participant_ids = {user.pk for user in obj.participants.all()}
        return bool(member_ids) and member_ids <= participant_ids

    def get_created_by_name(self, obj) -> str | None:
        return display_name(obj.created_by) if obj.created_by else None


class OccurrenceScopeSerializer(serializers.Serializer):
    occurrence = serializers.DateField(
        required=False,
        help_text='Solo eventos que se repiten: el día (AAAA-MM-DD) que se tocó en el calendario.',
    )
    scope = serializers.ChoiceField(
        choices=[SCOPE_ALL, SCOPE_THIS, SCOPE_FOLLOWING],
        default=SCOPE_ALL,
        help_text='«this» solo ese día, «following» ese día y los siguientes, «all» toda la serie.',
    )

    def validate(self, attrs):
        if attrs['scope'] != SCOPE_ALL and 'occurrence' not in attrs:
            raise serializers.ValidationError({'occurrence': 'Say which day of the series to change.'})
        return attrs


class EventFilterSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False, help_text='Eventos que empiezan desde este día.')
    date_to = serializers.DateField(required=False, help_text='Eventos que empiezan hasta este día.')


class CalendarRangeSerializer(serializers.Serializer):
    # «from» es palabra reservada en Python: se lee con get_fields
    def get_fields(self):
        return {
            'from': serializers.DateField(help_text='Primer día (AAAA-MM-DD).'),
            'to': serializers.DateField(help_text=f'Último día (AAAA-MM-DD). Hasta {CALENDAR_MAX_DAYS} días.'),
        }

    def validate(self, attrs):
        if attrs['to'] < attrs['from']:
            raise serializers.ValidationError({'to': '"to" must be on or after "from".'})
        if attrs['to'] - attrs['from'] >= timedelta(days=CALENDAR_MAX_DAYS):
            raise serializers.ValidationError({'to': f'The range can be at most {CALENDAR_MAX_DAYS} days.'})
        return attrs


class AgendaItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[ITEM_EVENT, ITEM_TASK, ITEM_EXPENSE])
    id = serializers.UUIDField(help_text='Evento, tarea o serie de gasto.')
    date = serializers.DateField()
    title = serializers.CharField()
    all_day = serializers.BooleanField(help_text='Sin hora: eventos de todo el día, tareas y gastos.')
    start_time = serializers.TimeField(allow_null=True, format='%H:%M')
    end_time = serializers.TimeField(allow_null=True, format='%H:%M')
    recurring = serializers.BooleanField()
    people = PersonSerializer(many=True, help_text='Participantes, responsables o quien paga.')
    everyone = serializers.BooleanField(help_text='Son todos los miembros del hogar.')
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    expense_id = serializers.UUIDField(
        allow_null=True,
        help_text='Gasto: el último gasto creado de la serie, para abrir su detalle.',
    )
    scheduled = serializers.BooleanField(
        help_text='Todavía no existe: próxima repetición de una tarea o de un gasto que se repite.',
    )
    reminder_minutes = serializers.IntegerField(
        allow_null=True,
        help_text='Evento: minutos antes en que avisa (null = sin recordatorio). Siempre null en tareas y gastos.',
    )


class CalendarSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    # Días con algo, para el punto de la cuadrícula
    busy_days = serializers.ListField(child=serializers.DateField())
    items = AgendaItemSerializer(many=True)
