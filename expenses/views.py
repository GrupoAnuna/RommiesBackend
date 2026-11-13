import csv
import io

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, mixins, renderers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from households.mixins import HOUSEHOLD_PARAM, HouseholdPagination, HouseholdScopedMixin
from households.views import UUID_REGEX

from .balances import compute_balances
from .models import Expense, Payment
from .serializers import BalancesSerializer, DateRangeFilterSerializer, ExpenseSerializer, PaymentSerializer

EXPENSE_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del gasto.')
PAYMENT_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del reembolso.')

CSV_HEADER = ['Fecha', 'Tipo', 'Concepto', 'Total', 'Pagó', 'Para', 'Monto']
CSV_EXPENSE = 'Gasto'
CSV_PAYMENT = 'Reembolso'

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


def filter_by_dates(request, queryset, field):
    params = DateRangeFilterSerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    filters = params.validated_data
    if 'from' in filters:
        queryset = queryset.filter(**{f'{field}__gte': filters['from']})
    if 'to' in filters:
        queryset = queryset.filter(**{f'{field}__lte': filters['to']})
    return queryset


@extend_schema(tags=['Expenses'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar gastos del hogar',
        description='Del más reciente al más antiguo. Paginados de 50 en 50 (?page=, ?page_size= hasta 100).',
        parameters=[DateRangeFilterSerializer],
    ),
    create=extend_schema(
        summary='Registrar gasto',
        description=(
            'Cualquier miembro. Envía `participants` con lo que le toca a cada uno (debe sumar exactamente '
            'el total) o `split: {"mode": "equal", "users": [...]}` para que el backend reparta en partes '
            'iguales. Los montos van como texto con dos decimales («120.50»).'
        ),
    ),
    retrieve=extend_schema(summary='Ver gasto', parameters=[EXPENSE_PARAM]),
    update=extend_schema(summary='Reemplazar gasto', parameters=[EXPENSE_PARAM]),
    partial_update=extend_schema(
        summary='Editar gasto',
        description=(
            'Si se envían `participants` o `split`, reemplazan el reparto anterior. Si solo cambia el total, '
            'hay que mandar también el reparto nuevo. Los saldos se recalculan solos.'
        ),
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
            queryset = filter_by_dates(self.request, queryset, 'spent_on')
        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        paid_by = serializer.validated_data.get('paid_by', user)
        serializer.save(household=self.household, created_by=user, paid_by=paid_by)

    @extend_schema(
        summary='Exportar gastos y reembolsos a CSV',
        description=(
            'Una fila por participante de cada gasto y una por reembolso, del más antiguo al más reciente. '
            'UTF-8 con BOM para que Excel muestre bien los acentos; separado por comas y con punto decimal.'
        ),
        parameters=[DateRangeFilterSerializer],
        responses={(200, 'text/csv'): OpenApiResponse(OpenApiTypes.STR, description='Archivo CSV.')},
    )
    @action(detail=False, methods=['get'], renderer_classes=[renderers.JSONRenderer, CSVRenderer])
    def export(self, request, household_pk=None):
        rows = []
        for expense in self.get_queryset():
            for participant in expense.participants.all():
                rows.append((expense.spent_on, expense.created_at, [
                    CSV_EXPENSE,
                    csv_cell(expense.title),
                    expense.amount,
                    csv_cell(person_name(expense.paid_by)),
                    csv_cell(person_name(participant.user)),
                    participant.amount,
                ]))
        payments = filter_by_dates(
            request,
            Payment.objects.filter(household=self.household).select_related('from_user', 'to_user'),
            'paid_on',
        )
        for payment in payments:
            rows.append((payment.paid_on, payment.created_at, [
                CSV_PAYMENT,
                csv_cell(payment.note),
                payment.amount,
                csv_cell(person_name(payment.from_user)),
                csv_cell(person_name(payment.to_user)),
                payment.amount,
            ]))
        # Por día y, dentro del día, por cuándo se registró (sort es estable: participantes en orden)
        rows.sort(key=lambda row: (row[0], row[1]))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for day, _, cells in rows:
            writer.writerow([day.isoformat(), *cells])

        params = request.query_params
        period = '_'.join(filter(None, [params.get('from'), params.get('to')])) or timezone.localdate().isoformat()
        response = HttpResponse('﻿' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="gastos-{period}.csv"'
        return response


@extend_schema(tags=['Payments'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar reembolsos del hogar',
        description='Del más reciente al más antiguo. Paginados de 50 en 50.',
        parameters=[DateRangeFilterSerializer],
    ),
    create=extend_schema(
        summary='Registrar reembolso',
        description=(
            'Cualquier miembro. `from_user` le pasó `amount` a `to_user` (por defecto `from_user` es quien lo '
            'registra). Cuenta en los saldos al momento.'
        ),
    ),
    retrieve=extend_schema(summary='Ver reembolso', parameters=[PAYMENT_PARAM]),
    destroy=extend_schema(
        summary='Borrar reembolso',
        description='Para deshacer uno registrado por error. Quien lo registró, quien pagó, quien recibió, o un admin.',
        parameters=[PAYMENT_PARAM],
    ),
)
class PaymentViewSet(
    HouseholdScopedMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PaymentSerializer
    pagination_class = HouseholdPagination
    lookup_value_regex = UUID_REGEX

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        involved = request.user.pk in (obj.created_by_id, obj.from_user_id, obj.to_user_id)
        if self.action == 'destroy' and not (involved or self.membership.is_admin):
            raise PermissionDenied('Only the people involved or a household admin can delete a payment.')

    def get_queryset(self):
        if self.is_schema_view:
            return Payment.objects.none()
        queryset = Payment.objects.filter(household=self.household).select_related('from_user', 'to_user')
        if self.action == 'list':
            queryset = filter_by_dates(self.request, queryset, 'paid_on')
        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        from_user = serializer.validated_data.get('from_user', user)
        serializer.save(household=self.household, created_by=user, from_user=from_user)


@extend_schema(
    tags=['Balances'],
    parameters=[HOUSEHOLD_PARAM],
    summary='Saldos del hogar',
    description=(
        'Se calculan en el momento con todos los gastos y reembolsos. Por persona: '
        'saldo = pagó − le toca + reembolsos enviados − reembolsos recibidos (positivo: le deben; '
        'negativo: debe). La suma de todos los saldos es 0. `suggested_transfers` dice quién le paga '
        'a quién para quedar todos en 0, con el menor número de pagos.'
    ),
    responses=BalancesSerializer,
)
class BalancesView(HouseholdScopedMixin, generics.GenericAPIView):
    serializer_class = BalancesSerializer

    def get(self, request, household_pk=None):
        return Response(self.get_serializer(compute_balances(self.household)).data)
