from datetime import timedelta

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from households.mixins import HOUSEHOLD_PARAM, HouseholdPagination, HouseholdScopedMixin
from households.views import UUID_REGEX

from .agenda import build_agenda
from .models import Event
from .occurrences import is_occurrence
from .serializers import (
    SCOPE_ALL,
    SCOPE_FOLLOWING,
    SCOPE_THIS,
    CalendarRangeSerializer,
    CalendarSerializer,
    EventFilterSerializer,
    EventSerializer,
    OccurrenceScopeSerializer,
)

EVENT_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del evento.')

# Campos que se copian al separar un día (o los siguientes) de una serie
COPIED_FIELDS = ['title', 'notes', 'all_day', 'start_time', 'end_time', 'reminder_minutes']
REPEAT_FIELDS = ['repeat_frequency', 'repeat_weekdays', 'repeat_until']

SCOPE_DESCRIPTION = (
    'Si el evento se repite, ?occurrence=AAAA-MM-DD&scope= dice qué cambiar: '
    '«this» solo ese día (sale de la serie como evento suelto), «following» ese día y los '
    'siguientes (la serie termina el día anterior y sigue una nueva desde ese día) o «all» '
    '(por defecto) toda la serie. Las repeticiones anteriores no se tocan.'
)


@extend_schema(tags=['Events'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar eventos del hogar',
        description=(
            'Los eventos tal como se guardaron (una serie es un solo evento). '
            'Para ver cada repetición día por día usa /calendar/. Paginados de 50 en 50.'
        ),
        parameters=[EventFilterSerializer],
    ),
    create=extend_schema(
        summary='Crear evento',
        description='Cualquier miembro. Al menos un participante, todos miembros del hogar.',
    ),
    retrieve=extend_schema(summary='Ver evento', parameters=[EVENT_PARAM]),
    update=extend_schema(
        summary='Reemplazar evento',
        description=SCOPE_DESCRIPTION,
        parameters=[EVENT_PARAM, OccurrenceScopeSerializer],
    ),
    partial_update=extend_schema(
        summary='Editar evento',
        description=f'Cualquier miembro. {SCOPE_DESCRIPTION} Devuelve el evento que quedó en ese día.',
        parameters=[EVENT_PARAM, OccurrenceScopeSerializer],
    ),
    destroy=extend_schema(
        summary='Borrar evento',
        description=f'Quien lo creó, o un admin u owner. {SCOPE_DESCRIPTION}',
        parameters=[EVENT_PARAM, OccurrenceScopeSerializer],
    ),
)
class EventViewSet(HouseholdScopedMixin, viewsets.ModelViewSet):
    serializer_class = EventSerializer
    pagination_class = HouseholdPagination
    lookup_value_regex = UUID_REGEX

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if self.action == 'destroy' and not (obj.created_by_id == request.user.pk or self.membership.is_admin):
            raise PermissionDenied('Only the creator or a household admin can delete this event.')

    def get_queryset(self):
        if self.is_schema_view:
            return Event.objects.none()

        queryset = Event.objects.filter(household=self.household).prefetch_related('participants')
        if self.action == 'list':
            params = EventFilterSerializer(data=self.request.query_params)
            params.is_valid(raise_exception=True)
            if 'date_from' in params.validated_data:
                queryset = queryset.filter(date__gte=params.validated_data['date_from'])
            if 'date_to' in params.validated_data:
                queryset = queryset.filter(date__lte=params.validated_data['date_to'])
        return queryset

    def perform_create(self, serializer):
        serializer.save(household=self.household, created_by=self.request.user)

    def _scope(self, event):
        """(scope, occurrence) de la URL; «all» si el evento no se repite."""
        params = OccurrenceScopeSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        scope = params.validated_data['scope']
        occurrence = params.validated_data.get('occurrence')
        if scope == SCOPE_ALL or not event.is_recurring:
            return SCOPE_ALL, None
        if not is_occurrence(event, occurrence):
            raise ValidationError({'occurrence': 'The event does not happen on that day.'})
        if scope == SCOPE_FOLLOWING and occurrence == event.date:
            # Desde el primer día es toda la serie
            return SCOPE_ALL, None
        return scope, occurrence

    def _copy_data(self, event, occurrence, keep_repeat):
        data = {name: getattr(event, name) for name in COPIED_FIELDS}
        data['participants'] = [user.pk for user in event.participants.all()]
        data['date'] = occurrence
        if keep_repeat and not (event.repeat_until and event.repeat_until <= occurrence):
            data.update({name: getattr(event, name) for name in REPEAT_FIELDS})
        else:
            # Solo ese día (o era la última repetición): queda suelto
            data['repeat_frequency'] = ''
        data.update(self.request.data.items() if hasattr(self.request.data, 'getlist') else self.request.data)
        return data

    def update(self, request, *args, **kwargs):
        partial = kwargs.get('partial', False)
        event = self.get_object()
        scope, occurrence = self._scope(event)
        if scope == SCOPE_ALL:
            return super().update(request, *args, **kwargs)

        keep_repeat = scope == SCOPE_FOLLOWING
        data = self._copy_data(event, occurrence, keep_repeat) if partial else dict(request.data.items())
        if not keep_repeat:
            # «Solo este» siempre queda como evento suelto, aunque la app mande la regla
            data['repeat_frequency'] = ''
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            if scope == SCOPE_THIS:
                new_event = serializer.save(
                    household=self.household,
                    created_by=event.created_by,
                    series=event,
                    original_date=occurrence,
                )
                self._exclude(event, occurrence)
            else:
                new_event = serializer.save(household=self.household, created_by=event.created_by)
                self._split(event, occurrence, new_event)

        return Response(self.get_serializer(new_event).data)

    def destroy(self, request, *args, **kwargs):
        event = self.get_object()
        scope, occurrence = self._scope(event)
        with transaction.atomic():
            if scope == SCOPE_ALL:
                event.delete()
            elif scope == SCOPE_THIS:
                self._exclude(event, occurrence)
            else:
                self._split(event, occurrence, None)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _exclude(event, day):
        dates = set(event.excluded_dates)
        dates.add(day.isoformat())
        event.excluded_dates = sorted(dates)
        event.save(update_fields=['excluded_dates', 'updated_at'])

    @staticmethod
    def _split(event, day, new_series):
        """La serie termina el día anterior a `day`; lo de después pasa a `new_series` o se borra."""
        later = event.overrides.filter(original_date__gte=day)
        moved_dates = [value for value in event.excluded_dates if value >= day.isoformat()]
        if new_series is None:
            later.delete()
        else:
            later.update(series=new_series)
            if moved_dates:
                new_series.excluded_dates = moved_dates
                new_series.save(update_fields=['excluded_dates'])
        event.repeat_until = day - timedelta(days=1)
        event.excluded_dates = [value for value in event.excluded_dates if value < day.isoformat()]
        event.save(update_fields=['repeat_until', 'excluded_dates', 'updated_at'])


@extend_schema(
    tags=['Events'],
    summary='Calendario del hogar',
    description=(
        'Lo que pasa cada día del rango: repeticiones de eventos, tareas pendientes (y los '
        'próximos días de las que se repiten) y gastos que se repiten (creados o programados). '
        'Ordenado por día; en cada día primero lo que no tiene hora y luego los eventos por '
        'hora de inicio. busy_days trae los días con algo, para el punto de la cuadrícula.'
    ),
    parameters=[HOUSEHOLD_PARAM, CalendarRangeSerializer],
    responses=CalendarSerializer,
)
class CalendarView(HouseholdScopedMixin, generics.GenericAPIView):
    serializer_class = CalendarSerializer

    def get(self, request, household_pk=None):
        params = CalendarRangeSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        agenda = build_agenda(self.household, params.validated_data['from'], params.validated_data['to'])
        return Response(self.get_serializer(agenda).data)
