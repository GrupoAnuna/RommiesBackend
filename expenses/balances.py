"""Saldos del hogar al estilo Tricount.

Todo se calcula en centavos enteros para que las sumas cuadren siempre.

    saldo = lo que pagó − lo que le toca + reembolsos que envió − reembolsos que recibió

Saldo positivo: le deben. Negativo: debe. La suma de todos los saldos es 0.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum

from households.models import Membership

from .models import Expense, ExpenseParticipant, Payment

User = get_user_model()

CENT = Decimal('0.01')


def to_cents(amount):
    return int((amount / CENT).to_integral_value())


def from_cents(cents):
    return (Decimal(cents) * CENT).quantize(CENT)


def split_equally(amount, users):
    """Reparte `amount` en partes iguales. Los centavos que sobran van a los primeros de la lista.

    100.00 entre 3 → 33.34, 33.33, 33.33
    """
    base, remainder = divmod(to_cents(amount), len(users))
    return [(user, from_cents(base + (1 if i < remainder else 0))) for i, user in enumerate(users)]


@dataclass
class MemberBalance:
    user: object
    is_member: bool
    paid: int = 0
    share: int = 0
    sent: int = 0
    received: int = 0

    @property
    def balance(self):
        return self.paid - self.share + self.sent - self.received


@dataclass
class HouseholdBalances:
    members: list
    total_spent: int
    transfers: list = field(default_factory=list)

    def for_user(self, user):
        return next((m for m in self.members if m.user.pk == user.pk), None)


def _sums(queryset, key):
    return {row[key]: to_cents(row['total']) for row in queryset.values(key).annotate(total=Sum('amount'))}


def compute_balances(household):
    paid = _sums(Expense.objects.filter(household=household), 'paid_by')
    share = _sums(ExpenseParticipant.objects.filter(expense__household=household), 'user')
    sent = _sums(Payment.objects.filter(household=household), 'from_user')
    received = _sums(Payment.objects.filter(household=household), 'to_user')

    active_ids = set(
        household.memberships.filter(status=Membership.STATUS_ACTIVE).values_list('user_id', flat=True)
    )
    # También quien ya no es miembro pero tiene movimientos: su saldo sigue contando
    user_ids = active_ids | set(paid) | set(share) | set(sent) | set(received)
    users = User.objects.in_bulk(user_ids)

    members = [
        MemberBalance(
            user=users[pk],
            is_member=pk in active_ids,
            paid=paid.get(pk, 0),
            share=share.get(pk, 0),
            sent=sent.get(pk, 0),
            received=received.get(pk, 0),
        )
        for pk in user_ids
    ]
    # Los que ya no son miembros solo aparecen si todavía deben o les deben
    members = [m for m in members if m.is_member or m.balance != 0]
    members.sort(key=lambda m: (-m.balance, (m.user.name or m.user.email).lower(), str(m.user.pk)))

    return HouseholdBalances(
        members=members,
        total_spent=sum(paid.values()),
        transfers=suggest_transfers(members),
    )


def suggest_transfers(members):
    """Pocas transferencias para dejar a todos en 0 (como máximo n − 1).

    Empareja siempre al que más debe con el que más le deben.
    """
    creditors = [[m.user, m.balance] for m in members if m.balance > 0]
    debtors = [[m.user, -m.balance] for m in members if m.balance < 0]
    transfers = []
    while creditors and debtors:
        creditors.sort(key=lambda c: (-c[1], str(c[0].pk)))
        debtors.sort(key=lambda d: (-d[1], str(d[0].pk)))
        creditor, debtor = creditors[0], debtors[0]
        cents = min(creditor[1], debtor[1])
        transfers.append((debtor[0], creditor[0], cents))
        creditor[1] -= cents
        debtor[1] -= cents
        if creditor[1] == 0:
            creditors.pop(0)
        if debtor[1] == 0:
            debtors.pop(0)
    return transfers


def user_balance(household, user):
    """Saldo en centavos de una persona en el hogar (0 si no tiene movimientos)."""
    member = compute_balances(household).for_user(user)
    return member.balance if member else 0
