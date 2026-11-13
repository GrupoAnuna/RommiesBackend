import csv
import io

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import renderers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from households.mixins import HOUSEHOLD_PARAM, HouseholdPagination, HouseholdScopedMixin
from households.views import UUID_REGEX

from .models import Expense
from .serializers import STATUS_PENDING, STATUS_SETTLED, ExpenseFilterSerializer, ExpenseSerializer

EXPENSE_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del gasto.')

CSV_HEADER = ['Fecha', 'Concepto', 'Total', 'Pagó', 'Participante', 'Le toca', 'Estado', 'Liquidado el']

# Una celda que empieza así la interpreta Excel como fórmula (inyección CSV)
FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


class CSVRenderer(renderers.BaseRenderer):
    """Solo para negociar `Accept: text/csv`; la acción arma su propia respuesta."""

    media_type = 'text/csv'
    format = 'csv'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def csv_cell(value):
    text = '' if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def person_name(user):
    return user.name or user.email


@extend_schema(tags=['Expenses'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar gastos del hogar',
        description='Del más reciente al más antiguo. Paginados de 50 en 50 (?page=, ?page_size= hasta 100).',
        parameters=[ExpenseFilterSerializer],
    ),
    create=extend_schema(
        summary='Registrar gasto',
        description=(
            'Cualquier miembro. Lo que le toca a cada participante debe sumar exactamente el total. '
            'Los montos van como texto con dos decimales («120.50»).'
        ),
    ),
    retrieve=extend_schema(summary='Ver gasto', parameters=[EXPENSE_PARAM]),
    update=extend_schema(summary='Reemplazar gasto', description='No se puede si ya está liquidado.', parameters=[EXPENSE_PARAM]),
    partial_update=extend_schema(
        summary='Editar gasto',
        description='No se puede si ya está liquidado. Si se envían participantes, reemplazan a los anteriores.',
        parameters=[EXPENSE_PARAM],
    ),
    destroy=extend_schema(
        summary='Borrar gasto',
        description='Quien lo registró, o un admin u owner.',
        parameters=[EXPENSE_PARAM],
    ),
)
class ExpenseViewSet(HouseholdScopedMixin, viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    pagination_class = HouseholdPagination
    lookup_value_regex = UUID_REGEX

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if self.action == 'destroy' and not (obj.created_by_id == request.user.pk or self.membership.is_admin):
            raise PermissionDenied('Only whoever recorded the expense or a household admin can delete it.')

    def get_queryset(self):
        if self.is_schema_view:
            return Expense.objects.none()
        queryset = (
            Expense.objects.filter(household=self.household)
            .select_related('paid_by')
            .prefetch_related('participants__user')
        )
        if self.action in ('list', 'export'):
            queryset = self._filter(queryset)
        return queryset

    def _filter(self, queryset):
        params = ExpenseFilterSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        filters = params.validated_data

        if filters.get('status') == STATUS_PENDING:
            queryset = queryset.filter(settled_at__isnull=True)
        elif filters.get('status') == STATUS_SETTLED:
            queryset = queryset.filter(settled_at__isnull=False)
        if 'from' in filters:
            queryset = queryset.filter(spent_on__gte=filters['from'])
        if 'to' in filters:
            queryset = queryset.filter(spent_on__lte=filters['to'])
        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        paid_by = serializer.validated_data.get('paid_by', user)
        serializer.save(household=self.household, created_by=user, paid_by=paid_by)

    @extend_schema(
        summary='Marcar gasto como liquidado',
        description='Cualquier miembro. Si ya estaba liquidado no cambia nada.',
        request=None,
        parameters=[EXPENSE_PARAM],
        responses=ExpenseSerializer,
    )
    @action(detail=True, methods=['post'])
    def settle(self, request, household_pk=None, pk=None):
        with transaction.atomic():
            expense = self.get_object()
            if not expense.is_settled:
                expense.settled_at = timezone.now()
                expense.settled_by = request.user
                expense.save(update_fields=['settled_at', 'settled_by', 'updated_at'])
        return Response(self.get_serializer(expense).data)

    @extend_schema(
        summary='Exportar gastos a CSV',
        description=(
            'Una fila por participante, del más antiguo al más reciente. UTF-8 con BOM para que Excel '
            'muestre bien los acentos; separado por comas y con punto decimal.'
        ),
        parameters=[ExpenseFilterSerializer],
        responses={(200, 'text/csv'): OpenApiResponse(OpenApiTypes.STR, description='Archivo CSV.')},
    )
    @action(detail=False, methods=['get'], renderer_classes=[renderers.JSONRenderer, CSVRenderer])
    def export(self, request, household_pk=None):
        expenses = self.get_queryset().order_by('spent_on', 'created_at')

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for expense in expenses:
            settled_on = timezone.localtime(expense.settled_at).date().isoformat() if expense.settled_at else ''
            for participant in expense.participants.all():
                writer.writerow([
                    expense.spent_on.isoformat(),
                    csv_cell(expense.title),
                    expense.amount,
                    csv_cell(person_name(expense.paid_by)),
                    csv_cell(person_name(participant.user)),
                    participant.amount,
                    'Liquidado' if expense.is_settled else 'Pendiente',
                    settled_on,
                ])

        params = request.query_params
        period = '_'.join(filter(None, [params.get('from'), params.get('to')])) or timezone.localdate().isoformat()
        response = HttpResponse('﻿' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="gastos-{period}.csv"'
        return response
