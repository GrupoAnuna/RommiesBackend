from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from households.models import Membership

from .models import AMOUNT_DECIMALS, AMOUNT_DIGITS, Expense, ExpenseParticipant

User = get_user_model()

STATUS_PENDING = 'pending'
STATUS_SETTLED = 'settled'


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


class ExpenseSerializer(serializers.ModelSerializer):
    amount = amount_serializer_field()
    paid_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
        help_text='Quién pagó; por defecto, quien registra el gasto.',
        error_messages={'does_not_exist': 'The payer must be an active member of this household.'},
    )
    paid_by_name = serializers.CharField(source='paid_by.name', read_only=True)
    participants = ExpenseParticipantSerializer(many=True)
    is_settled = serializers.BooleanField(read_only=True)

    class Meta:
        model = Expense
        fields = [
            'id', 'household', 'title', 'amount', 'paid_by', 'paid_by_name', 'spent_on',
            'participants', 'is_settled', 'settled_at', 'settled_by',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['household', 'settled_at', 'settled_by', 'created_by', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Quien pagó y los participantes: solo miembros activos del hogar de la URL
        household = self.context.get('household')
        if household is not None:
            members = household_members(household)
            self.fields['paid_by'].queryset = members
            self.fields['participants'].child.fields['user'].queryset = members

    def validate_participants(self, value):
        if not value:
            raise serializers.ValidationError('Add at least one participant.')
        users = [p['user'].pk for p in value]
        if len(users) != len(set(users)):
            raise serializers.ValidationError('Each person can appear only once.')
        return value

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_settled:
            raise serializers.ValidationError('Settled expenses cannot be edited.')

        # La suma de lo que le toca a cada uno debe ser exactamente el total
        amount = attrs.get('amount', self.instance.amount if self.instance else None)
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


class ExpenseFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[STATUS_PENDING, STATUS_SETTLED], required=False)

    def get_fields(self):
        # «from» es palabra reservada en Python: los campos se declaran aquí
        fields = super().get_fields()
        fields['from'] = serializers.DateField(required=False, help_text='Gastos desde este día (AAAA-MM-DD).')
        fields['to'] = serializers.DateField(required=False, help_text='Gastos hasta este día (AAAA-MM-DD).')
        return fields

    def validate(self, attrs):
        if 'from' in attrs and 'to' in attrs and attrs['from'] > attrs['to']:
            raise serializers.ValidationError({'to': 'Must be on or after "from".'})
        return attrs
