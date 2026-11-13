from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from households.models import Membership

from .balances import from_cents, split_equally
from .models import (
    AMOUNT_DECIMALS,
    AMOUNT_DIGITS,
    REPEAT_CHOICES,
    REPEAT_WEEKLY,
    SPLIT_AMOUNTS,
    SPLIT_EQUAL,
    Expense,
    ExpenseParticipant,
    ExpensePayer,
    Payment,
    Receipt,
    RecurringExpense,
)
from .recurrence import template_of

User = get_user_model()

SCOPE_THIS = 'this'
SCOPE_FOLLOWING = 'following'

# Foto del ticket: la app la comprime antes de subirla
RECEIPT_MAX_BYTES = 5 * 1024 * 1024
RECEIPT_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def amount_serializer_field(**kwargs):
    # Se envía y se devuelve como texto («120.50») para no perder centavos
    return serializers.DecimalField(
        max_digits=AMOUNT_DIGITS,
        decimal_places=AMOUNT_DECIMALS,
        min_value=Decimal('0.01'),
        **kwargs,
    )


def household_members(household):
    return User.objects.filter(
        memberships__household=household,
        memberships__status=Membership.STATUS_ACTIVE,
    )


def display_name(user):
    return user.name or user.email


def total_of(rows):
    return sum((row['amount'] for row in rows), Decimal('0'))


def check_unique_users(value):
    users = [row['user'].pk for row in value]
    if len(users) != len(set(users)):
        raise serializers.ValidationError('Each person can appear only once.')
    return value


class ExpenseShareSerializer(serializers.Serializer):
    """Una persona y un monto: quién pagó cuánto o a quién le toca cuánto."""

    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        error_messages={'does_not_exist': 'Each person must be an active member of this household.'},
    )
    user_name = serializers.SerializerMethodField()
    amount = amount_serializer_field()

    def get_user_name(self, obj) -> str:
        return display_name(obj.user)


class SplitSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=[SPLIT_EQUAL], help_text='Por ahora solo «equal»: partes iguales.')
    users = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.none(),
        help_text='Entre quiénes se reparte. Los centavos que sobran van a los primeros.',
    )

    def validate_users(self, value):
        if not value:
            raise serializers.ValidationError('Add at least one person.')
        if len({user.pk for user in value}) != len(value):
            raise serializers.ValidationError('Each person can appear only once.')
        return value


class RepeatSerializer(serializers.Serializer):
    frequency = serializers.ChoiceField(choices=REPEAT_CHOICES)
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
        help_text='Solo «weekly»: días de la semana, 0 = lunes. Mensual: el día del mes de la fecha del gasto.',
    )
    ends_on = serializers.DateField(required=False, allow_null=True, help_text='Último día en que puede repetirse.')

    def validate(self, attrs):
        weekdays = sorted(set(attrs.get('weekdays', [])))
        if attrs['frequency'] == REPEAT_WEEKLY and not weekdays:
            raise serializers.ValidationError({'weekdays': 'Choose at least one day.'})
        attrs['weekdays'] = weekdays if attrs['frequency'] == REPEAT_WEEKLY else []
        return attrs


class ExpenseSerializer(serializers.ModelSerializer):
    amount = amount_serializer_field()
    payers = ExpenseShareSerializer(
        many=True,
        required=False,
        help_text='Quién pagó y cuánto; debe sumar el total. Por defecto, quien lo registra pagó todo.',
    )
    participants = ExpenseShareSerializer(
        many=True,
        required=False,
        help_text='Lo que le toca a cada uno; debe sumar el total. Usa esto o `split`, no ambos.',
    )
    split = SplitSerializer(
        write_only=True,
        required=False,
        help_text='Reparto automático: el backend calcula `participants`.',
    )
    receipt = serializers.PrimaryKeyRelatedField(
        queryset=Receipt.objects.none(),
        required=False,
        allow_null=True,
        help_text='Id de la foto subida en /receipts/. null la quita.',
    )
    repeat = RepeatSerializer(required=False, allow_null=True, help_text='null o ausente: no se repite.')
    created_by_name = serializers.SerializerMethodField()
    my_share = serializers.SerializerMethodField(help_text='Lo que te toca; null si no participas.')
    my_settlement = serializers.SerializerMethodField(help_text='Si ya saldaste tu parte: cuándo y con quién.')

    class Meta:
        model = Expense
        fields = [
            'id', 'household', 'title', 'amount', 'category', 'spent_on', 'split_mode',
            'payers', 'participants', 'split', 'receipt', 'repeat', 'series',
            'my_share', 'my_settlement', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['household', 'split_mode', 'series', 'created_by', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pagadores, participantes y ticket: solo del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            members = household_members(household)
            self.fields['payers'].child.fields['user'].queryset = members
            self.fields['participants'].child.fields['user'].queryset = members
            self.fields['split'].fields['users'].child_relation.queryset = members
            self.fields['receipt'].queryset = Receipt.objects.filter(household=household)

    @property
    def _me(self):
        request = self.context.get('request')
        return request.user if request else None

    def get_created_by_name(self, obj) -> str | None:
        return display_name(obj.created_by) if obj.created_by else None

    def get_my_share(self, obj) -> str | None:
        me = self._me
        share = next((p for p in obj.participants.all() if me and p.user_id == me.pk), None)
        return str(share.amount) if share else None

    def get_my_settlement(self, obj) -> dict | None:
        me = self._me
        payments = [p for p in obj.settlements.all() if me and p.from_user_id == me.pk]
        if not payments:
            return None
        return {
            'paid_on': max(p.paid_on for p in payments).isoformat(),
            'to_names': [display_name(p.to_user) for p in payments],
        }

    def validate_payers(self, value):
        if not value:
            raise serializers.ValidationError('Add at least one payer.')
        return check_unique_users(value)

    def validate_participants(self, value):
        if not value:
            raise serializers.ValidationError('Add at least one participant.')
        return check_unique_users(value)

    def validate(self, attrs):
        instance = self.instance
        amount = attrs.get('amount', instance.amount if instance else None)
        amount_changed = instance is not None and 'amount' in attrs and attrs['amount'] != instance.amount

        # Quién pagó: por defecto quien lo registra; con un solo pagador sigue al total
        if 'payers' not in attrs:
            if instance is None:
                attrs['payers'] = [{'user': self._me, 'amount': amount}]
            elif amount_changed:
                payers = list(instance.payers.all())
                if len(payers) == 1:
                    attrs['payers'] = [{'user': payers[0].user, 'amount': amount}]
        if 'payers' in attrs and total_of(attrs['payers']) != amount:
            raise serializers.ValidationError(
                {'payers': f'The payers must add up to the total ({amount}); they add up to {total_of(attrs["payers"])}.'}
            )

        # Reparto: `split` lo calcula; en partes iguales también se recalcula si cambia el total
        split = attrs.pop('split', None)
        if split is not None:
            if 'participants' in attrs:
                raise serializers.ValidationError({'split': 'Send either participants or split, not both.'})
            users = split['users']
            if amount < Decimal('0.01') * len(users):
                raise serializers.ValidationError(
                    {'split': f'The total is too small to split among {len(users)} people.'}
                )
            attrs['participants'] = [{'user': user, 'amount': share} for user, share in split_equally(amount, users)]
            attrs['split_mode'] = SPLIT_EQUAL
        elif 'participants' in attrs:
            attrs['split_mode'] = SPLIT_AMOUNTS
        elif instance is None:
            raise serializers.ValidationError({'participants': 'Send participants or split.'})
        elif amount_changed and instance.split_mode == SPLIT_EQUAL:
            users = [p.user for p in instance.participants.order_by('user__name', 'user_id')]
            attrs['participants'] = [{'user': user, 'amount': share} for user, share in split_equally(amount, users)]

        # La suma de lo que le toca a cada uno debe ser exactamente el total
        participants = attrs.get('participants')
        if participants is not None:
            shares = total_of(participants)
        elif instance is not None:
            shares = sum((p.amount for p in instance.participants.all()), Decimal('0'))
        else:
            shares = None
        if shares is not None and shares != amount:
            raise serializers.ValidationError(
                {'participants': f'The participants must add up to the total ({amount}); they add up to {shares}.'}
            )

        spent_on = attrs.get('spent_on', instance.spent_on if instance else None)
        repeat = attrs.get('repeat')
        if repeat and repeat.get('ends_on') and spent_on and repeat['ends_on'] <= spent_on:
            raise serializers.ValidationError({'repeat': {'ends_on': 'Must be after the expense date.'}})
        if instance is not None and instance.series_id and 'spent_on' in attrs and attrs['spent_on'] != instance.spent_on:
            if instance.series.occurrences.filter(spent_on=attrs['spent_on']).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError({'spent_on': 'This series already has an expense on that day.'})
        return attrs

    def _write_rows(self, expense, payers, participants):
        if payers is not None:
            expense.payers.all().delete()
            ExpensePayer.objects.bulk_create(
                ExpensePayer(expense=expense, user=row['user'], amount=row['amount']) for row in payers
            )
        if participants is not None:
            expense.participants.all().delete()
            ExpenseParticipant.objects.bulk_create(
                ExpenseParticipant(expense=expense, user=row['user'], amount=row['amount']) for row in participants
            )

    def _start_series(self, expense, repeat):
        series = RecurringExpense.objects.create(
            household=expense.household,
            frequency=repeat['frequency'],
            weekdays=repeat['weekdays'],
            day_of_month=expense.spent_on.day,
            starts_on=expense.spent_on,
            ends_on=repeat.get('ends_on'),
            last_date=expense.spent_on,
            template=template_of(expense),
            created_by=expense.created_by,
        )
        expense.series = series
        expense.save(update_fields=['series'])

    @transaction.atomic
    def create(self, validated_data):
        payers = validated_data.pop('payers')
        participants = validated_data.pop('participants')
        repeat = validated_data.pop('repeat', None)
        expense = Expense.objects.create(**validated_data)
        self._write_rows(expense, payers, participants)
        if repeat:
            self._start_series(expense, repeat)
        return expense

    @transaction.atomic
    def update(self, instance, validated_data):
        payers = validated_data.pop('payers', None)
        participants = validated_data.pop('participants', None)
        has_repeat = 'repeat' in validated_data
        repeat = validated_data.pop('repeat', None)
        expense = super().update(instance, validated_data)
        self._write_rows(expense, payers, participants)

        series = expense.series
        if series is None:
            if repeat:
                self._start_series(expense, repeat)
        elif self.context.get('scope') == SCOPE_FOLLOWING:
            # Este y los siguientes: las próximas repeticiones salen de esta versión
            series.template = template_of(expense)
            fields = ['template']
            if has_repeat and repeat is None:
                series.active = False
                series.ends_on = expense.spent_on
                fields += ['active', 'ends_on']
            elif repeat:
                series.frequency = repeat['frequency']
                series.weekdays = repeat['weekdays']
                series.day_of_month = expense.spent_on.day
                series.ends_on = repeat.get('ends_on')
                fields += ['frequency', 'weekdays', 'day_of_month', 'ends_on']
            series.save(update_fields=fields)
        return expense


class ReceiptSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True, help_text='JPEG, PNG o WEBP de hasta 5 MB.')

    class Meta:
        model = Receipt
        fields = ['id', 'image', 'created_at']
        read_only_fields = ['created_at']

    def validate_image(self, value):
        if value.size > RECEIPT_MAX_BYTES:
            raise serializers.ValidationError('The photo must be 5 MB or smaller.')
        image_format = getattr(getattr(value, 'image', None), 'format', None)
        if image_format not in RECEIPT_FORMATS:
            raise serializers.ValidationError('Use a JPEG, PNG or WEBP photo.')
        return value


class ScopeSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(
        choices=[SCOPE_THIS, SCOPE_FOLLOWING],
        default=SCOPE_THIS,
        help_text='Gastos que se repiten: «this» solo este, «following» este y los siguientes.',
    )


class DateRangeFilterSerializer(serializers.Serializer):
    def get_fields(self):
        # «from» es palabra reservada en Python: los campos se declaran aquí
        fields = super().get_fields()
        fields['from'] = serializers.DateField(required=False, help_text='Desde este día (AAAA-MM-DD).')
        fields['to'] = serializers.DateField(required=False, help_text='Hasta este día (AAAA-MM-DD).')
        return fields

    def validate(self, attrs):
        if 'from' in attrs and 'to' in attrs and attrs['from'] > attrs['to']:
            raise serializers.ValidationError({'to': 'Must be on or after "from".'})
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    from_user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        help_text='Quién pagó; por defecto, quien lo registra.',
        error_messages={'does_not_exist': 'Both people must be active members of this household.'},
    )
    from_name = serializers.SerializerMethodField()
    to_user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        help_text='Quién recibió el dinero.',
        error_messages={'does_not_exist': 'Both people must be active members of this household.'},
    )
    to_name = serializers.SerializerMethodField()
    amount = amount_serializer_field()
    expense_title = serializers.CharField(source='expense.title', read_only=True, default=None)

    class Meta:
        model = Payment
        fields = [
            'id', 'household', 'from_user', 'from_name', 'to_user', 'to_name', 'amount', 'paid_on', 'note',
            'expense', 'expense_title', 'created_by', 'created_at',
        ]
        read_only_fields = ['household', 'expense', 'created_by', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        household = self.context.get('household')
        if household is not None:
            members = household_members(household)
            self.fields['from_user'].queryset = members
            self.fields['to_user'].queryset = members

    def get_from_name(self, obj) -> str:
        return display_name(obj.from_user)

    def get_to_name(self, obj) -> str:
        return display_name(obj.to_user)

    def validate(self, attrs):
        me = self.context['request'].user
        from_user = attrs.get('from_user') or me
        if from_user.pk == attrs['to_user'].pk:
            raise serializers.ValidationError({'to_user': 'A person cannot pay themselves.'})
        # Solo las dos personas involucradas registran el pago
        if me.pk not in (from_user.pk, attrs['to_user'].pk):
            raise serializers.ValidationError('Only the two people involved can register this payment.')
        return attrs


def money(cents):
    # Mismo formato que los montos de gastos: texto con dos decimales
    return str(from_cents(cents))


class MemberBalanceSerializer(serializers.Serializer):
    user = serializers.UUIDField(source='user.pk')
    name = serializers.SerializerMethodField()
    is_member = serializers.BooleanField(help_text='False si ya salió del hogar pero aún debe o le deben.')
    paid = serializers.SerializerMethodField(help_text='Lo que pagó en gastos.')
    share = serializers.SerializerMethodField(help_text='Lo que le toca de los gastos.')
    sent = serializers.SerializerMethodField(help_text='Reembolsos que envió.')
    received = serializers.SerializerMethodField(help_text='Reembolsos que recibió.')
    balance = serializers.SerializerMethodField(help_text='Positivo: le deben. Negativo: debe.')

    def get_name(self, obj) -> str:
        return display_name(obj.user)

    def get_paid(self, obj) -> str:
        return money(obj.paid)

    def get_share(self, obj) -> str:
        return money(obj.share)

    def get_sent(self, obj) -> str:
        return money(obj.sent)

    def get_received(self, obj) -> str:
        return money(obj.received)

    def get_balance(self, obj) -> str:
        return money(obj.balance)


class TransferSerializer(serializers.Serializer):
    from_user = serializers.UUIDField()
    from_name = serializers.CharField()
    to_user = serializers.UUIDField()
    to_name = serializers.CharField()
    amount = serializers.CharField(help_text='Texto con dos decimales.')

    def to_representation(self, transfer):
        debtor, creditor, cents = transfer
        return {
            'from_user': str(debtor.pk),
            'from_name': display_name(debtor),
            'to_user': str(creditor.pk),
            'to_name': display_name(creditor),
            'amount': money(cents),
        }


class BalancesSerializer(serializers.Serializer):
    total_spent = serializers.SerializerMethodField(help_text='Suma de todos los gastos del hogar.')
    my_balance = serializers.SerializerMethodField(help_text='Saldo de quien consulta.')
    members = MemberBalanceSerializer(many=True)
    suggested_transfers = TransferSerializer(
        source='transfers',
        many=True,
        help_text='Pagos mínimos para dejar a todos en 0. Al hacer uno, regístralo en /payments/.',
    )

    def get_total_spent(self, obj) -> str:
        return money(obj.total_spent)

    def get_my_balance(self, obj) -> str:
        member = obj.for_user(self.context['request'].user)
        return money(member.balance if member else 0)


class ActivityItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=['expense', 'payment'])
    expense = ExpenseSerializer(allow_null=True)
    payment = PaymentSerializer(allow_null=True)


class ActivitySerializer(serializers.Serializer):
    month = serializers.CharField(help_text='AAAA-MM; null si el hogar no tiene movimientos.', allow_null=True)
    total = serializers.CharField(help_text='Suma de los gastos del mes (sin reembolsos).')
    items = ActivityItemSerializer(many=True)
    previous_month = serializers.CharField(allow_null=True, help_text='Mes anterior con movimientos, para cargar más.')


class MonthFilterSerializer(serializers.Serializer):
    month = serializers.RegexField(
        r'^\d{4}-(0[1-9]|1[0-2])$',
        required=False,
        help_text='AAAA-MM. Sin él: el mes más reciente con movimientos.',
    )
