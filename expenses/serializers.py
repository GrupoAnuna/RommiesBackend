from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from households.models import Membership

from .balances import from_cents, split_equally
from .models import AMOUNT_DECIMALS, AMOUNT_DIGITS, Expense, ExpenseParticipant, Payment

User = get_user_model()

SPLIT_EQUAL = 'equal'


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


class ExpenseParticipantSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        error_messages={'does_not_exist': 'Each participant must be an active member of this household.'},
    )
    user_name = serializers.CharField(source='user.name', read_only=True)
    amount = amount_serializer_field()

    class Meta:
        model = ExpenseParticipant
        fields = ['user', 'user_name', 'amount']


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


class ExpenseSerializer(serializers.ModelSerializer):
    amount = amount_serializer_field()
    paid_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        help_text='Quién pagó; por defecto, quien registra el gasto.',
        error_messages={'does_not_exist': 'The payer must be an active member of this household.'},
    )
    paid_by_name = serializers.CharField(source='paid_by.name', read_only=True)
    participants = ExpenseParticipantSerializer(
        many=True,
        required=False,
        help_text='Lo que le toca a cada uno; debe sumar el total. Usa esto o `split`, no ambos.',
    )
    split = SplitSerializer(
        write_only=True,
        required=False,
        help_text='Reparto automático: el backend calcula `participants`.',
    )

    class Meta:
        model = Expense
        fields = [
            'id', 'household', 'title', 'amount', 'paid_by', 'paid_by_name', 'spent_on',
            'participants', 'split', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['household', 'created_by', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Quien pagó y los participantes: solo miembros activos del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            members = household_members(household)
            self.fields['paid_by'].queryset = members
            self.fields['participants'].child.fields['user'].queryset = members
            self.fields['split'].fields['users'].child_relation.queryset = members

    def validate_participants(self, value):
        if not value:
            raise serializers.ValidationError('Add at least one participant.')
        users = [p['user'].pk for p in value]
        if len(users) != len(set(users)):
            raise serializers.ValidationError('Each person can appear only once.')
        return value

    def validate(self, attrs):
        amount = attrs.get('amount', self.instance.amount if self.instance else None)
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
        elif self.instance is None and 'participants' not in attrs:
            raise serializers.ValidationError({'participants': 'Send participants or split.'})

        # La suma de lo que le toca a cada uno debe ser exactamente el total
        participants = attrs.get('participants')
        if participants is not None:
            shares = sum((p['amount'] for p in participants), Decimal('0'))
        elif self.instance is not None:
            shares = sum((p.amount for p in self.instance.participants.all()), Decimal('0'))
        else:
            shares = None
        if shares is not None and shares != amount:
            raise serializers.ValidationError(
                {'participants': f'The participants must add up to the total ({amount}); they add up to {shares}.'}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        participants = validated_data.pop('participants')
        expense = Expense.objects.create(**validated_data)
        ExpenseParticipant.objects.bulk_create(
            ExpenseParticipant(expense=expense, **participant) for participant in participants
        )
        return expense

    @transaction.atomic
    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        expense = super().update(instance, validated_data)
        if participants is not None:
            expense.participants.all().delete()
            ExpenseParticipant.objects.bulk_create(
                ExpenseParticipant(expense=expense, **participant) for participant in participants
            )
        return expense


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
    from_name = serializers.CharField(source='from_user.name', read_only=True)
    to_user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        help_text='Quién recibió el dinero.',
        error_messages={'does_not_exist': 'Both people must be active members of this household.'},
    )
    to_name = serializers.CharField(source='to_user.name', read_only=True)
    amount = amount_serializer_field()

    class Meta:
        model = Payment
        fields = [
            'id', 'household', 'from_user', 'from_name', 'to_user', 'to_name', 'amount', 'paid_on', 'note',
            'created_by', 'created_at',
        ]
        read_only_fields = ['household', 'created_by', 'created_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        household = self.context.get('household')
        if household is not None:
            members = household_members(household)
            self.fields['from_user'].queryset = members
            self.fields['to_user'].queryset = members

    def validate(self, attrs):
        request = self.context.get('request')
        from_user = attrs.get('from_user') or (request.user if request else None)
        if from_user is not None and from_user.pk == attrs['to_user'].pk:
            raise serializers.ValidationError({'to_user': 'A person cannot pay themselves.'})
        return attrs


def money(cents):
    # Mismo formato que los montos de gastos: texto con dos decimales
    return str(from_cents(cents))


def display_name(user):
    return user.name or user.email


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
