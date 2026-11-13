import csv
import io
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, mixins, parsers, renderers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from households.mixins import HOUSEHOLD_PARAM, HouseholdPagination, HouseholdScopedMixin
from households.views import UUID_REGEX

from .balances import compute_balances, split_proportionally
from .models import Expense, Payment, Receipt
from .recurrence import generate_due
from .serializers import (
    SCOPE_FOLLOWING,
    ActivitySerializer,
    BalancesSerializer,
    DateRangeFilterSerializer,
    ExpenseSerializer,
    MonthFilterSerializer,
    PaymentSerializer,
    ReceiptSerializer,
    ScopeSerializer,
    display_name,
)

EXPENSE_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del gasto.')
PAYMENT_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del reembolso.')
RECEIPT_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id de la foto.')

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


def filter_by_dates(request, queryset, field):
    params = DateRangeFilterSerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    filters = params.validated_data
    if 'from' in filters:
        queryset = queryset.filter(**{f'{field}__gte': filters['from']})
    if 'to' in filters:
        queryset = queryset.filter(**{f'{field}__lte': filters['to']})
    return queryset


def expenses_of(household):
    return (
        Expense.objects.filter(household=household)
        .select_related('created_by', 'series')
        .prefetch_related('payers__user', 'participants__user', 'settlements__to_user')
    )


def payments_of(household):
    return Payment.objects.filter(household=household).select_related('from_user', 'to_user', 'expense')


class RecurringMixin:
    """Antes de mostrar gastos o saldos, crea las repeticiones que tocan hasta hoy."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not self.is_schema_view:
            generate_due(self.household)


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
            'Cualquier miembro. `payers`: quién pagó y cuánto (por defecto, quien lo registra pagó todo). '
            'Reparto: `participants` con lo que le toca a cada uno o `split: {"mode": "equal", "users": [...]}`. '
            'Ambos deben sumar el total. `repeat` lo vuelve recurrente. Montos como texto («120.50»).'
        ),
    ),
    retrieve=extend_schema(summary='Ver gasto', parameters=[EXPENSE_PARAM]),
    update=extend_schema(summary='Reemplazar gasto', parameters=[EXPENSE_PARAM, ScopeSerializer]),
    partial_update=extend_schema(
        summary='Editar gasto',
        description=(
            'Solo quien lo registró. `payers`, `participants` o `split` reemplazan los anteriores. Si cambia el '
            'total, con un solo pagador o en partes iguales se ajustan solos. En un gasto que se repite, '
            '`?scope=following` cambia también los siguientes (y `repeat: null` termina la serie).'
        ),
        parameters=[EXPENSE_PARAM, ScopeSerializer],
    ),
    destroy=extend_schema(
        summary='Borrar gasto',
        description=(
            'Quien lo registró, o un admin u owner. En un gasto que se repite, `?scope=following` borra '
            'también los siguientes y termina la serie; los anteriores se quedan.'
        ),
        parameters=[EXPENSE_PARAM, ScopeSerializer],
    ),
)
class ExpenseViewSet(HouseholdScopedMixin, RecurringMixin, viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    pagination_class = HouseholdPagination
    lookup_value_regex = UUID_REGEX

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        mine = obj.created_by_id == request.user.pk
        if self.action in ('update', 'partial_update') and not mine:
            raise PermissionDenied('Only whoever recorded the expense can edit it.')
        if self.action == 'destroy' and not (mine or self.membership.is_admin):
            raise PermissionDenied('Only whoever recorded the expense or a household admin can delete it.')

    def get_queryset(self):
        if self.is_schema_view:
            return Expense.objects.none()
        queryset = expenses_of(self.household)
        if self.action in ('list', 'export'):
            queryset = filter_by_dates(self.request, queryset, 'spent_on')
        return queryset

    def _scope(self):
        params = ScopeSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        return params.validated_data['scope']

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in ('update', 'partial_update'):
            context['scope'] = self._scope()
        return context

    def perform_create(self, serializer):
        serializer.save(household=self.household, created_by=self.request.user)
        # Si la fecha es pasada y se repite, aparecen ya las repeticiones hasta hoy
        generate_due(self.household)

    @transaction.atomic
    def perform_destroy(self, instance):
        series = instance.series
        if series is not None and self._scope() == SCOPE_FOLLOWING:
            series.occurrences.filter(spent_on__gt=instance.spent_on).delete()
            series.active = False
            series.ends_on = instance.spent_on - timedelta(days=1)
            series.save(update_fields=['active', 'ends_on'])
        instance.delete()

    @extend_schema(
        summary='Saldar tu parte',
        description=(
            'Registra que le pagaste tu parte a quien pagó el gasto. Si pagaron varios, tu parte se reparte '
            'entre ellos en proporción a lo que puso cada uno (un reembolso por persona, ligados al gasto).'
        ),
        request=None,
        parameters=[EXPENSE_PARAM],
        responses=ExpenseSerializer,
    )
    @action(detail=True, methods=['post'], url_path='settle-share')
    def settle_share(self, request, household_pk=None, pk=None):
        expense = self.get_object()
        me = request.user
        share = next((p for p in expense.participants.all() if p.user_id == me.pk), None)
        payers = [p for p in expense.payers.all() if p.user_id != me.pk]
        if share is None:
            raise ValidationError({'detail': 'You are not part of this expense.'})
        if len(payers) != len(expense.payers.all()):
            raise ValidationError({'detail': 'You paid this expense; there is nothing to settle.'})
        if expense.settlements.filter(from_user=me).exists():
            raise ValidationError({'detail': 'You already settled your share.'})

        amounts = split_proportionally(share.amount, [p.amount for p in payers])
        with transaction.atomic():
            Payment.objects.bulk_create(
                Payment(
                    household=self.household,
                    from_user=me,
                    to_user=payer.user,
                    amount=amount,
                    expense=expense,
                    note=expense.title[:200],
                    created_by=me,
                )
                for payer, amount in zip(payers, amounts)
                if amount > 0
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=expense.pk)).data)

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
            paid_by = ', '.join(display_name(p.user) for p in expense.payers.all())
            for participant in expense.participants.all():
                rows.append((expense.spent_on, expense.created_at, [
                    CSV_EXPENSE,
                    csv_cell(expense.title),
                    expense.amount,
                    csv_cell(paid_by),
                    csv_cell(display_name(participant.user)),
                    participant.amount,
                ]))
        for payment in filter_by_dates(request, payments_of(self.household), 'paid_on'):
            rows.append((payment.paid_on, payment.created_at, [
                CSV_PAYMENT,
                csv_cell(payment.note),
                payment.amount,
                csv_cell(display_name(payment.from_user)),
                csv_cell(display_name(payment.to_user)),
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
            'Solo las dos personas involucradas: quien pagó o quien recibió. `from_user` le pasó `amount` a '
            '`to_user` (por defecto `from_user` es quien lo registra). Cuenta en los saldos al momento.'
        ),
    ),
    retrieve=extend_schema(summary='Ver reembolso', parameters=[PAYMENT_PARAM]),
    destroy=extend_schema(
        summary='Borrar reembolso',
        description='Para deshacer uno registrado por error. Solo quien lo registró.',
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
        if self.action == 'destroy' and obj.created_by_id != request.user.pk:
            raise PermissionDenied('Only whoever recorded the payment can delete it.')

    def get_queryset(self):
        if self.is_schema_view:
            return Payment.objects.none()
        queryset = payments_of(self.household)
        if self.action == 'list':
            queryset = filter_by_dates(self.request, queryset, 'paid_on')
        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        from_user = serializer.validated_data.get('from_user', user)
        serializer.save(household=self.household, created_by=user, from_user=from_user)


@extend_schema(tags=['Expenses'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    create=extend_schema(
        summary='Subir foto del ticket',
        description=(
            'multipart/form-data con `image` (JPEG, PNG o WEBP, hasta 5 MB). Devuelve el id que va en '
            '`receipt` al crear o editar el gasto.'
        ),
    ),
    retrieve=extend_schema(
        summary='Ver foto del ticket',
        description='Devuelve la imagen. Solo miembros del hogar.',
        parameters=[RECEIPT_PARAM],
        responses={(200, 'image/*'): OpenApiResponse(OpenApiTypes.BINARY, description='La foto.')},
    ),
)
class ReceiptViewSet(HouseholdScopedMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = ReceiptSerializer
    parser_classes = [parsers.MultiPartParser]
    lookup_value_regex = UUID_REGEX

    def get_queryset(self):
        if self.is_schema_view:
            return Receipt.objects.none()
        return Receipt.objects.filter(household=self.household)

    def perform_create(self, serializer):
        serializer.save(household=self.household, uploaded_by=self.request.user)

    def retrieve(self, request, household_pk=None, pk=None):
        receipt = self.get_object()
        return FileResponse(receipt.image.open('rb'))


def parse_month(value):
    year, month = value.split('-')
    return date(int(year), int(month), 1)


def next_month(day):
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


@extend_schema(
    tags=['Expenses'],
    parameters=[HOUSEHOLD_PARAM, MonthFilterSerializer],
    summary='Actividad del mes',
    description=(
        'Gastos y reembolsos de un mes, mezclados del más reciente al más antiguo. Sin `month`, el mes '
        'más reciente con movimientos. `previous_month` es el mes anterior que tiene movimientos (para '
        'cargar más); null si no hay.'
    ),
    responses=ActivitySerializer,
)
class ActivityView(HouseholdScopedMixin, RecurringMixin, generics.GenericAPIView):
    serializer_class = ActivitySerializer

    def get(self, request, household_pk=None):
        params = MonthFilterSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        expenses = expenses_of(self.household)
        payments = payments_of(self.household)

        month = params.validated_data.get('month')
        start = parse_month(month) if month else self._latest_month(expenses, payments)
        if start is None:
            return Response({'month': None, 'total': '0.00', 'items': [], 'previous_month': None})
        end = next_month(start)

        month_expenses = expenses.filter(spent_on__gte=start, spent_on__lt=end)
        month_payments = payments.filter(paid_on__gte=start, paid_on__lt=end)
        context = self.get_serializer_context()
        items = [
            (e.spent_on, e.created_at, {'type': 'expense', 'expense': ExpenseSerializer(e, context=context).data, 'payment': None})
            for e in month_expenses
        ] + [
            (p.paid_on, p.created_at, {'type': 'payment', 'expense': None, 'payment': PaymentSerializer(p, context=context).data})
            for p in month_payments
        ]
        items.sort(key=lambda item: (item[0], item[1]), reverse=True)

        total = month_expenses.aggregate(total=Sum('amount'))['total']
        previous = self._latest_month(expenses.filter(spent_on__lt=start), payments.filter(paid_on__lt=start))
        return Response({
            'month': start.strftime('%Y-%m'),
            'total': f'{total or 0:.2f}',
            'items': [item for _, _, item in items],
            'previous_month': previous.strftime('%Y-%m') if previous else None,
        })

    @staticmethod
    def _latest_month(expenses, payments):
        months = [
            expenses.annotate(m=TruncMonth('spent_on')).order_by('-m').values_list('m', flat=True).first(),
            payments.annotate(m=TruncMonth('paid_on')).order_by('-m').values_list('m', flat=True).first(),
        ]
        months = [m for m in months if m is not None]
        return max(months) if months else None


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
class BalancesView(HouseholdScopedMixin, RecurringMixin, generics.GenericAPIView):
    serializer_class = BalancesSerializer

    def get(self, request, household_pk=None):
        return Response(self.get_serializer(compute_balances(self.household)).data)
