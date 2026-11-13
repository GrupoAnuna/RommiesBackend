import csv
import io
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from households.models import Household, Membership

from .balances import split_equally, split_proportionally
from .models import (
    REPEAT_MONTHLY,
    REPEAT_WEEKLY,
    Expense,
    ExpenseParticipant,
    ExpensePayer,
    Payment,
    Receipt,
    RecurringExpense,
)
from .recurrence import create_occurrence, due_dates, generate_due, month_day

User = get_user_model()

DAY = date(2026, 11, 20)


def list_url(household):
    return f'/api/v1/households/{household.pk}/expenses/'


def detail_url(expense):
    return f'/api/v1/households/{expense.household_id}/expenses/{expense.pk}/'


def export_url(household):
    return f'{list_url(household)}export/'


def payments_url(household):
    return f'/api/v1/households/{household.pk}/payments/'


def receipts_url(household):
    return f'/api/v1/households/{household.pk}/receipts/'


def activity_url(household):
    return f'/api/v1/households/{household.pk}/activity/'


def balances_url(household):
    return f'/api/v1/households/{household.pk}/balances/'


class ExpenseTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='owner@example.com', firebase_uid='uid-owner', name='Olga')
        self.admin = User.objects.create_user(email='admin@example.com', firebase_uid='uid-admin', name='Ana')
        self.member = User.objects.create_user(email='member@example.com', firebase_uid='uid-member', name='Memo')
        self.outsider = User.objects.create_user(email='outsider@example.com', firebase_uid='uid-outsider')

        self.household = Household.objects.create(name='Piso Centro', owner=self.owner)
        Membership.objects.create(household=self.household, user=self.owner, role=Membership.ROLE_OWNER)
        Membership.objects.create(household=self.household, user=self.admin, role=Membership.ROLE_ADMIN)
        Membership.objects.create(household=self.household, user=self.member, role=Membership.ROLE_MEMBER)

        self.other_household = Household.objects.create(name='Ajeno', owner=self.outsider)
        Membership.objects.create(household=self.other_household, user=self.outsider, role=Membership.ROLE_OWNER)

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def make_expense(self, title='Súper', amount='90.00', spent_on=DAY, shares=None, paid_by=None, **fields):
        fields.setdefault('created_by', self.member)
        expense = Expense.objects.create(
            household=self.household, title=title, amount=Decimal(amount), spent_on=spent_on, **fields,
        )
        ExpensePayer.objects.create(expense=expense, user=paid_by or self.member, amount=Decimal(amount))
        for user, share in shares or [(self.member, amount)]:
            ExpenseParticipant.objects.create(expense=expense, user=user, amount=Decimal(share))
        return expense

    def payload(self, **fields):
        data = {
            'title': 'Súper del mes',
            'amount': '90.00',
            'spent_on': DAY.isoformat(),
            'participants': [
                {'user': str(self.member.pk), 'amount': '30.00'},
                {'user': str(self.owner.pk), 'amount': '30.00'},
                {'user': str(self.admin.pk), 'amount': '30.00'},
            ],
        }
        data.update(fields)
        return data


class AccessTests(ExpenseTestCase):
    def test_requires_authentication(self):
        self.assertEqual(self.client.get(list_url(self.household)).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_outsider_gets_404(self):
        expense = self.make_expense()
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(list_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(detail_url(expense)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(export_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)


class CreateTests(ExpenseTestCase):
    def test_member_records_expense_with_participants(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # Montos como texto con dos decimales, nunca float
        self.assertEqual(response.data['amount'], '90.00')
        # Por defecto pagó todo quien lo registra
        self.assertEqual(response.data['payers'], [{'user': self.member.pk, 'user_name': 'Memo', 'amount': '90.00'}])
        self.assertEqual(response.data['created_by'], self.member.pk)
        self.assertEqual(response.data['my_share'], '30.00')
        self.assertIsNone(response.data['my_settlement'])
        self.assertEqual(response.data['split_mode'], 'amounts')
        self.assertEqual({p['user_name'] for p in response.data['participants']}, {'Memo', 'Olga', 'Ana'})
        expense = Expense.objects.get(pk=response.data['id'])
        self.assertIsInstance(expense.amount, Decimal)
        self.assertEqual(expense.participants.count(), 3)

    def test_paid_by_another_member(self):
        self.as_user(self.member)
        payers = [{'user': str(self.owner.pk), 'amount': '90.00'}]
        response = self.client.post(list_url(self.household), self.payload(payers=payers), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['payers'][0]['user_name'], 'Olga')

    def test_several_payers_must_add_up(self):
        self.as_user(self.member)
        payers = [{'user': str(self.member.pk), 'amount': '50.00'}, {'user': str(self.owner.pk), 'amount': '40.00'}]
        response = self.client.post(list_url(self.household), self.payload(payers=payers), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(ExpensePayer.objects.count(), 2)

        payers[1]['amount'] = '30.00'
        response = self.client.post(list_url(self.household), self.payload(payers=payers), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('payers', response.data)

        payers[1] = dict(payers[0])
        response = self.client.post(list_url(self.household), self.payload(payers=payers), format='json')
        self.assertIn('payers', response.data)

    def test_category(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(category='supermercado'), format='json')
        self.assertEqual(response.data['category'], 'supermercado')
        response = self.client.post(list_url(self.household), self.payload(category='viajes'), format='json')
        self.assertIn('category', response.data)

    def test_participants_must_add_up_to_total(self):
        self.as_user(self.member)
        data = self.payload(amount='100.00')
        response = self.client.post(list_url(self.household), data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('participants', response.data)
        self.assertFalse(Expense.objects.exists())

    def test_cents_are_exact(self):
        # 100 entre 3: 33.34 + 33.33 + 33.33 suma exacto en Decimal
        self.as_user(self.member)
        data = self.payload(amount='100.00', participants=[
            {'user': str(self.member.pk), 'amount': '33.34'},
            {'user': str(self.owner.pk), 'amount': '33.33'},
            {'user': str(self.admin.pk), 'amount': '33.33'},
        ])
        response = self.client.post(list_url(self.household), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_invalid_amounts(self):
        self.as_user(self.member)
        for amount in ('0', '-5.00', '10.005', 'diez'):
            response = self.client.post(list_url(self.household), self.payload(amount=amount), format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, amount)
            self.assertIn('amount', response.data)

    def test_needs_participants_without_repeats(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(participants=[]), format='json')
        self.assertIn('participants', response.data)

        repeated = [
            {'user': str(self.member.pk), 'amount': '45.00'},
            {'user': str(self.member.pk), 'amount': '45.00'},
        ]
        response = self.client.post(list_url(self.household), self.payload(participants=repeated), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('participants', response.data)

    def test_payer_and_participants_must_be_members(self):
        self.as_user(self.member)
        payers = [{'user': str(self.outsider.pk), 'amount': '90.00'}]
        response = self.client.post(list_url(self.household), self.payload(payers=payers), format='json')
        self.assertIn('payers', response.data)

        data = self.payload(amount='30.00', participants=[{'user': str(self.outsider.pk), 'amount': '30.00'}])
        response = self.client.post(list_url(self.household), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('participants', response.data)

    def test_atomic_when_a_participant_fails(self):
        # Si falla al guardar un participante no queda el gasto a medias
        self.as_user(self.member)
        original = ExpenseParticipant.objects.bulk_create

        def boom(*args, **kwargs):
            raise RuntimeError('fallo al guardar')

        ExpenseParticipant.objects.bulk_create = boom
        try:
            with self.assertRaises(RuntimeError):
                self.client.post(list_url(self.household), self.payload(), format='json')
        finally:
            ExpenseParticipant.objects.bulk_create = original
        self.assertFalse(Expense.objects.exists())


class EditTests(ExpenseTestCase):
    def test_replace_participants(self):
        expense = self.make_expense(amount='90.00')
        self.as_user(self.member)
        response = self.client.patch(detail_url(expense), {
            'amount': '60.00',
            'participants': [
                {'user': str(self.member.pk), 'amount': '20.00'},
                {'user': str(self.owner.pk), 'amount': '40.00'},
            ],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(expense.participants.count(), 2)

    def test_changing_total_alone_must_still_add_up(self):
        expense = self.make_expense(amount='90.00')
        self.as_user(self.member)
        response = self.client.patch(detail_url(expense), {'amount': '80.00'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('participants', response.data)

        response = self.client.patch(detail_url(expense), {'title': 'Súper y farmacia'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_creator_or_admin(self):
        expense = self.make_expense(created_by=self.owner)
        self.as_user(self.member)
        self.assertEqual(self.client.delete(detail_url(expense)).status_code, status.HTTP_403_FORBIDDEN)

        mine = self.make_expense(created_by=self.member)
        self.assertEqual(self.client.delete(detail_url(mine)).status_code, status.HTTP_204_NO_CONTENT)

        self.as_user(self.admin)
        self.assertEqual(self.client.delete(detail_url(expense)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ExpenseParticipant.objects.exists())


class ListTests(ExpenseTestCase):
    def setUp(self):
        super().setUp()
        self.old = self.make_expense('Luz', spent_on=date(2026, 10, 3))
        self.mid = self.make_expense('Internet', spent_on=date(2026, 11, 1))
        self.new = self.make_expense('Súper', spent_on=date(2026, 11, 20))
        self.as_user(self.member)

    def titles(self, **params):
        response = self.client.get(list_url(self.household), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [e['title'] for e in response.data['results']]

    def test_newest_first(self):
        self.assertEqual(self.titles(), ['Súper', 'Internet', 'Luz'])

    def test_filters(self):
        self.assertEqual(self.titles(**{'from': '2026-11-01', 'to': '2026-11-30'}), ['Súper', 'Internet'])

    def test_invalid_filters(self):
        for params in ({'from': 'ayer'}, {'from': '2026-11-30', 'to': '2026-11-01'}):
            response = self.client.get(list_url(self.household), params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, params)


class ExportTests(ExpenseTestCase):
    def setUp(self):
        super().setUp()
        self.make_expense('Súper y café', amount='100.00', spent_on=date(2026, 11, 2),
                          shares=[(self.member, '66.67'), (self.owner, '33.33')])
        self.make_expense('=HYPERLINK("http://x")', amount='10.00', spent_on=date(2026, 11, 5))
        self.make_expense('Octubre', spent_on=date(2026, 10, 1))
        Payment.objects.create(household=self.household, from_user=self.owner, to_user=self.member,
                               amount=Decimal('33.33'), paid_on=date(2026, 11, 3), note='Transferencia')
        self.as_user(self.member)

    def export(self, **params):
        response = self.client.get(export_url(self.household), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def test_csv_with_bom_and_one_row_per_participant(self):
        response = self.export(**{'from': '2026-11-01', 'to': '2026-11-30'})

        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="gastos-2026-11-01_2026-11-30.csv"', response['Content-Disposition'])
        raw = response.content
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))  # BOM para Excel

        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['Fecha', 'Tipo', 'Concepto', 'Total', 'Pagó', 'Para', 'Monto'])
        self.assertEqual(rows[1], ['2026-11-02', 'Gasto', 'Súper y café', '100.00', 'Memo', 'Memo', '66.67'])
        self.assertEqual(rows[2][5:7], ['Olga', '33.33'])
        # El reembolso va en orden de fecha, entre los gastos
        self.assertEqual(rows[3], ['2026-11-03', 'Reembolso', 'Transferencia', '33.33', 'Olga', 'Memo', '33.33'])
        self.assertEqual(rows[4][1], 'Gasto')
        self.assertEqual(len(rows), 5)  # Octubre queda fuera

    def test_formulas_are_neutralized(self):
        rows = list(csv.reader(io.StringIO(self.export().content.decode('utf-8-sig'))))
        titles = [row[2] for row in rows[1:]]
        self.assertIn('\'=HYPERLINK("http://x")', titles)

    def test_accepts_text_csv(self):
        response = self.client.get(export_url(self.household), HTTP_ACCEPT='text/csv')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_range_is_json_error(self):
        response = self.client.get(export_url(self.household), {'from': '2026-12-01', 'to': '2026-11-01'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('to', response.json())


class SchemaTests(APITestCase):
    def test_openapi_documents_expense_endpoints(self):
        paths = self.client.get('/api/schema/', {'format': 'json'}).json()['paths']
        base = '/api/v1/households/{household_pk}/expenses/'
        payments = '/api/v1/households/{household_pk}/payments/'
        for path in (base, base + '{id}/', base + 'export/', payments, payments + '{id}/',
                     '/api/v1/households/{household_pk}/balances/'):
            self.assertIn(path, paths)
        self.assertNotIn(base + '{id}/settle/', paths)


class SplitTests(ExpenseTestCase):
    def split(self, *users):
        return {'mode': 'equal', 'users': [str(user.pk) for user in users]}

    def test_split_equally_spreads_leftover_cents(self):
        shares = split_equally(Decimal('100.00'), ['a', 'b', 'c'])
        self.assertEqual(shares, [('a', Decimal('33.34')), ('b', Decimal('33.33')), ('c', Decimal('33.33'))])
        self.assertEqual(split_equally(Decimal('0.05'), ['a', 'b'])[0][1], Decimal('0.03'))

    def test_create_with_equal_split(self):
        self.as_user(self.member)
        data = self.payload(amount='100.00', split=self.split(self.member, self.owner, self.admin))
        del data['participants']
        response = self.client.post(list_url(self.household), data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotIn('split', response.data)
        shares = {p['user_name']: p['amount'] for p in response.data['participants']}
        self.assertEqual(shares, {'Memo': '33.34', 'Olga': '33.33', 'Ana': '33.33'})

    def test_patch_total_with_split(self):
        expense = self.make_expense(amount='90.00')
        self.as_user(self.member)
        response = self.client.patch(
            detail_url(expense), {'amount': '50.00', 'split': self.split(self.member, self.owner)}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(sorted(p['amount'] for p in response.data['participants']), ['25.00', '25.00'])

    def test_needs_participants_or_split_but_not_both(self):
        self.as_user(self.member)
        data = self.payload()
        del data['participants']
        response = self.client.post(list_url(self.household), data, format='json')
        self.assertIn('participants', response.data)

        response = self.client.post(
            list_url(self.household), self.payload(split=self.split(self.member)), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('split', response.data)

    def test_invalid_splits(self):
        self.as_user(self.member)
        cases = [
            ('0.02', self.split(self.member, self.owner, self.admin)),  # no alcanza para un centavo cada uno
            ('90.00', self.split(self.member, self.member)),
            ('90.00', self.split()),
            ('90.00', self.split(self.outsider)),
            ('90.00', {'mode': 'shares', 'users': [str(self.member.pk)]}),
        ]
        for amount, split in cases:
            data = self.payload(amount=amount, split=split)
            del data['participants']
            response = self.client.post(list_url(self.household), data, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, split)
            self.assertIn('split', response.data)
        self.assertFalse(Expense.objects.exists())


class PaymentTests(ExpenseTestCase):
    def test_record_payment_defaults_to_me(self):
        self.as_user(self.member)
        response = self.client.post(
            payments_url(self.household),
            {'to_user': str(self.owner.pk), 'amount': '25.50', 'note': 'Bizum'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['from_user'], self.member.pk)
        self.assertEqual(response.data['to_name'], 'Olga')
        self.assertEqual(response.data['amount'], '25.50')
        self.assertEqual(response.data['created_by'], self.member.pk)

    def test_receiver_can_record_payment(self):
        # Quien recibió el dinero también puede registrarlo
        self.as_user(self.owner)
        response = self.client.post(
            payments_url(self.household),
            {'from_user': str(self.admin.pk), 'to_user': str(self.owner.pk), 'amount': '10.00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['from_user'], self.admin.pk)

    def test_cannot_record_payment_between_others(self):
        self.as_user(self.member)
        response = self.client.post(
            payments_url(self.household),
            {'from_user': str(self.admin.pk), 'to_user': str(self.owner.pk), 'amount': '10.00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Payment.objects.exists())

    def test_invalid_payments(self):
        self.as_user(self.member)
        cases = [
            ({'to_user': str(self.member.pk), 'amount': '10.00'}, 'to_user'),
            ({'to_user': str(self.outsider.pk), 'amount': '10.00'}, 'to_user'),
            ({'to_user': str(self.owner.pk), 'amount': '0'}, 'amount'),
            ({'to_user': str(self.owner.pk), 'amount': '1.001'}, 'amount'),
        ]
        for data, field in cases:
            response = self.client.post(payments_url(self.household), data, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, data)
            self.assertIn(field, response.data)
        self.assertFalse(Payment.objects.exists())

    def test_list_and_filter_by_date(self):
        Payment.objects.create(household=self.household, from_user=self.member, to_user=self.owner,
                               amount=Decimal('5.00'), paid_on=date(2026, 10, 1))
        Payment.objects.create(household=self.household, from_user=self.admin, to_user=self.owner,
                               amount=Decimal('7.00'), paid_on=date(2026, 11, 2))
        self.as_user(self.member)
        response = self.client.get(payments_url(self.household))
        self.assertEqual([p['amount'] for p in response.data['results']], ['7.00', '5.00'])
        response = self.client.get(payments_url(self.household), {'from': '2026-11-01'})
        self.assertEqual(response.data['count'], 1)

    def test_delete_only_whoever_recorded_it(self):
        payment = Payment.objects.create(household=self.household, from_user=self.admin, to_user=self.owner,
                                         amount=Decimal('5.00'), created_by=self.admin)
        url = f'{payments_url(self.household)}{payment.pk}/'
        self.as_user(self.owner)  # quien lo recibió
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_403_FORBIDDEN)
        self.as_user(self.admin)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)

    def test_no_editing_and_outsider_404(self):
        payment = Payment.objects.create(household=self.household, from_user=self.admin, to_user=self.owner,
                                         amount=Decimal('5.00'))
        url = f'{payments_url(self.household)}{payment.pk}/'
        self.as_user(self.admin)
        self.assertEqual(self.client.patch(url, {'amount': '1.00'}, format='json').status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(payments_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)


class BalanceTests(ExpenseTestCase):
    def balances(self, user=None):
        self.as_user(user or self.member)
        response = self.client.get(balances_url(self.household))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def by_name(self, data):
        return {m['name']: m for m in data['members']}

    def test_no_activity_everyone_at_zero(self):
        data = self.balances()
        self.assertEqual(data['total_spent'], '0.00')
        self.assertEqual(data['my_balance'], '0.00')
        self.assertEqual({m['balance'] for m in data['members']}, {'0.00'})
        self.assertEqual(len(data['members']), 3)
        self.assertEqual(data['suggested_transfers'], [])

    def test_balances_and_transfers(self):
        # Memo paga 90 entre los tres; Olga paga 30 solo para Ana
        self.make_expense(amount='90.00', paid_by=self.member,
                          shares=[(self.member, '30.00'), (self.owner, '30.00'), (self.admin, '30.00')])
        self.make_expense(amount='30.00', paid_by=self.owner, shares=[(self.admin, '30.00')])

        data = self.balances()
        members = self.by_name(data)
        self.assertEqual(data['total_spent'], '120.00')
        self.assertEqual(members['Memo']['paid'], '90.00')
        self.assertEqual(members['Memo']['share'], '30.00')
        self.assertEqual(members['Memo']['balance'], '60.00')
        self.assertEqual(members['Olga']['balance'], '0.00')
        self.assertEqual(members['Ana']['balance'], '-60.00')
        self.assertEqual(data['my_balance'], '60.00')
        # Primero quien más le deben
        self.assertEqual(data['members'][0]['name'], 'Memo')
        self.assertEqual(data['suggested_transfers'], [{
            'from_user': str(self.admin.pk), 'from_name': 'Ana',
            'to_user': str(self.member.pk), 'to_name': 'Memo', 'amount': '60.00',
        }])

    def test_payment_settles_up(self):
        self.make_expense(amount='90.00', paid_by=self.member,
                          shares=[(self.member, '30.00'), (self.owner, '30.00'), (self.admin, '30.00')])
        self.as_user(self.owner)
        self.client.post(payments_url(self.household),
                         {'to_user': str(self.member.pk), 'amount': '30.00'}, format='json')

        members = self.by_name(self.balances())
        self.assertEqual(members['Olga']['sent'], '30.00')
        self.assertEqual(members['Olga']['balance'], '0.00')
        self.assertEqual(members['Memo']['received'], '30.00')
        self.assertEqual(members['Memo']['balance'], '30.00')

        self.as_user(self.admin)
        self.client.post(payments_url(self.household),
                         {'to_user': str(self.member.pk), 'amount': '30.00'}, format='json')
        data = self.balances()
        self.assertEqual({m['balance'] for m in data['members']}, {'0.00'})
        self.assertEqual(data['suggested_transfers'], [])

    def test_balances_always_add_up_to_zero(self):
        self.make_expense(amount='100.00', paid_by=self.owner,
                          shares=[(self.member, '33.34'), (self.owner, '33.33'), (self.admin, '33.33')])
        self.make_expense(amount='17.99', paid_by=self.admin, shares=[(self.member, '17.99')])
        Payment.objects.create(household=self.household, from_user=self.member, to_user=self.owner,
                               amount=Decimal('12.34'))
        data = self.balances()
        total = sum(Decimal(m['balance']) for m in data['members'])
        self.assertEqual(total, Decimal('0'))

        # Hacer las transferencias sugeridas deja a todos en 0
        for transfer in data['suggested_transfers']:
            Payment.objects.create(household=self.household, from_user_id=transfer['from_user'],
                                   to_user_id=transfer['to_user'], amount=Decimal(transfer['amount']))
        self.assertEqual({m['balance'] for m in self.balances()['members']}, {'0.00'})

    def test_transfers_are_few(self):
        # Dos deudores y un acreedor: dos pagos, no más
        self.make_expense(amount='90.00', paid_by=self.owner,
                          shares=[(self.member, '45.00'), (self.admin, '45.00')])
        transfers = self.balances()['suggested_transfers']
        self.assertEqual(len(transfers), 2)
        self.assertEqual({t['to_name'] for t in transfers}, {'Olga'})

    def test_former_member_with_balance_still_appears(self):
        self.make_expense(amount='20.00', paid_by=self.owner, shares=[(self.member, '20.00')])
        Membership.objects.filter(user=self.member).delete()
        data = self.balances(self.owner)
        memo = self.by_name(data)['Memo']
        self.assertFalse(memo['is_member'])
        self.assertEqual(memo['balance'], '-20.00')

    def test_only_this_household(self):
        ajeno = Expense.objects.create(household=self.other_household, title='Ajeno', amount=Decimal('50.00'))
        ExpensePayer.objects.create(expense=ajeno, user=self.outsider, amount=Decimal('50.00'))
        self.assertEqual(self.balances()['total_spent'], '0.00')
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(balances_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)


class LeaveWithBalanceTests(ExpenseTestCase):
    def setUp(self):
        super().setUp()
        self.make_expense(amount='20.00', paid_by=self.owner, shares=[(self.member, '20.00')])

    def test_cannot_leave_with_balance(self):
        self.as_user(self.member)
        response = self.client.post(f'/api/v1/households/{self.household.pk}/leave/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.household.get_membership(self.member))

        Payment.objects.create(household=self.household, from_user=self.member, to_user=self.owner,
                               amount=Decimal('20.00'))
        response = self.client.post(f'/api/v1/households/{self.household.pk}/leave/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_cannot_remove_member_with_balance(self):
        self.as_user(self.owner)
        url = f'/api/v1/households/{self.household.pk}/members/{self.member.pk}/'
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_400_BAD_REQUEST)
        # Sin movimientos sí se puede
        admin_url = f'/api/v1/households/{self.household.pk}/members/{self.admin.pk}/'
        self.assertEqual(self.client.delete(admin_url).status_code, status.HTTP_204_NO_CONTENT)


class EditPermissionTests(ExpenseTestCase):
    def test_only_whoever_recorded_it_can_edit(self):
        expense = self.make_expense(created_by=self.owner)
        self.as_user(self.admin)
        response = self.client.patch(detail_url(expense), {'title': 'Otro'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.as_user(self.owner)
        response = self.client.patch(detail_url(expense), {'title': 'Otro'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_total_change_adjusts_single_payer_and_equal_split(self):
        self.as_user(self.member)
        data = self.payload(amount='90.00', split={'mode': 'equal', 'users': [str(self.member.pk), str(self.owner.pk)]})
        del data['participants']
        expense_id = self.client.post(list_url(self.household), data, format='json').data['id']
        expense = Expense.objects.get(pk=expense_id)
        response = self.client.patch(detail_url(expense), {'amount': '50.01'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['payers'][0]['amount'], '50.01')
        self.assertEqual(sorted(p['amount'] for p in response.data['participants']), ['25.00', '25.01'])
        self.assertEqual(response.data['split_mode'], 'equal')


class SettleShareTests(ExpenseTestCase):
    def settle(self, expense, user):
        self.as_user(user)
        return self.client.post(f'{detail_url(expense)}settle-share/')

    def test_settle_my_share_with_the_payer(self):
        expense = self.make_expense(amount='90.00', paid_by=self.owner,
                                    shares=[(self.member, '30.00'), (self.owner, '30.00'), (self.admin, '30.00')])
        response = self.settle(expense, self.member)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['my_settlement']['to_names'], ['Olga'])
        payment = Payment.objects.get()
        self.assertEqual((payment.from_user, payment.to_user, payment.amount, payment.expense),
                         (self.member, self.owner, Decimal('30.00'), expense))
        # Ya no debe nada de este gasto
        self.as_user(self.member)
        balances = self.client.get(balances_url(self.household)).data
        memo = next(m for m in balances['members'] if m['name'] == 'Memo')
        self.assertEqual(memo['balance'], '0.00')

        # Otra vez no
        self.assertEqual(self.settle(expense, self.member).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.count(), 1)

    def test_several_payers_get_it_in_proportion(self):
        expense = self.make_expense(amount='126.00', paid_by=self.owner,
                                    shares=[(self.member, '42.00'), (self.owner, '42.00'), (self.admin, '42.00')])
        expense.payers.all().delete()
        ExpensePayer.objects.create(expense=expense, user=self.owner, amount=Decimal('80.00'))
        ExpensePayer.objects.create(expense=expense, user=self.admin, amount=Decimal('46.00'))

        self.assertEqual(self.settle(expense, self.member).status_code, status.HTTP_200_OK)
        amounts = {p.to_user.name: p.amount for p in Payment.objects.all()}
        self.assertEqual(amounts, {'Olga': Decimal('26.67'), 'Ana': Decimal('15.33')})
        self.assertEqual(sum(amounts.values()), Decimal('42.00'))

    def test_cannot_settle_if_not_participant_or_payer(self):
        expense = self.make_expense(amount='30.00', paid_by=self.owner, shares=[(self.admin, '30.00')])
        self.assertEqual(self.settle(expense, self.member).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.settle(expense, self.owner).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Payment.objects.exists())

    def test_proportional_split_is_exact(self):
        parts = split_proportionally(Decimal('4200.00'), [Decimal('8000.00'), Decimal('4600.00')])
        self.assertEqual(parts, [Decimal('2666.67'), Decimal('1533.33')])
        self.assertEqual(sum(split_proportionally(Decimal('0.10'), [Decimal('1'), Decimal('1'), Decimal('1')])),
                         Decimal('0.10'))


class RecurringTests(ExpenseTestCase):
    def create(self, spent_on, repeat):
        self.as_user(self.member)
        data = self.payload(spent_on=spent_on.isoformat(), repeat=repeat)
        response = self.client.post(list_url(self.household), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Expense.objects.get(pk=response.data['id'])

    def test_monthly_uses_last_day_in_short_months(self):
        series = RecurringExpense(frequency=REPEAT_MONTHLY, day_of_month=31, last_date=date(2027, 1, 31))
        self.assertEqual(list(due_dates(series, date(2027, 4, 30))),
                         [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)])

    def test_weekly_on_chosen_days_until_end(self):
        # 2026-11-02 es lunes; lun y jue hasta el 12
        series = RecurringExpense(frequency=REPEAT_WEEKLY, weekdays=[0, 3], last_date=date(2026, 11, 2),
                                  ends_on=date(2026, 11, 12))
        self.assertEqual(list(due_dates(series, date(2026, 12, 31))),
                         [date(2026, 11, 5), date(2026, 11, 9), date(2026, 11, 12)])

    def test_past_monthly_expense_catches_up_once(self):
        # Día 1 de hace tres meses: ese, los dos siguientes y el de este mes
        today = timezone.localdate()
        month, year = today.month - 3, today.year
        if month <= 0:
            month, year = month + 12, year - 1
        expense = self.create(month_day(year, month, 1), {'frequency': 'monthly'})
        series = expense.series
        count = series.occurrences.count()
        self.assertEqual(count, 4)
        # Cada repetición copia pagadores y reparto
        last = series.occurrences.order_by('-spent_on').first()
        self.assertEqual(last.participants.count(), 3)
        self.assertEqual(last.payers.get().user, self.member)
        self.assertEqual(last.spent_on, today.replace(day=1))
        # Generar otra vez no duplica
        self.assertEqual(generate_due(self.household), 0)
        self.client.get(list_url(self.household))
        self.assertEqual(series.occurrences.count(), count)

    def test_command_generates_due_expenses(self):
        expense = self.create(timezone.localdate() - timedelta(days=14), {'frequency': 'weekly', 'weekdays': [0, 1, 2, 3, 4, 5, 6]})
        self.assertEqual(expense.series.occurrences.count(), 15)
        out = io.StringIO()
        call_command('generate_recurring_expenses', stdout=out)
        self.assertIn('0', out.getvalue())

    def test_repeat_validation(self):
        self.as_user(self.member)
        cases = [
            {'frequency': 'weekly', 'weekdays': []},
            {'frequency': 'daily'},
            {'frequency': 'monthly', 'ends_on': DAY.isoformat()},
        ]
        for repeat in cases:
            response = self.client.post(list_url(self.household), self.payload(repeat=repeat), format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, repeat)
            self.assertIn('repeat', response.data)

    def test_repeat_is_returned(self):
        expense = self.create(DAY, {'frequency': 'weekly', 'weekdays': [4, 0, 4], 'ends_on': '2027-03-09'})
        self.as_user(self.member)
        data = self.client.get(detail_url(expense)).data
        self.assertEqual(data['repeat'], {'frequency': 'weekly', 'weekdays': [0, 4], 'ends_on': '2027-03-09'})
        self.assertEqual(data['series'], expense.series_id)

    def test_edit_following_changes_next_ones(self):
        expense = self.create(DAY, {'frequency': 'monthly'})
        self.client.patch(f'{detail_url(expense)}?scope=following', {'title': 'Internet 2'}, format='json')
        self.assertEqual(RecurringExpense.objects.get().template['title'], 'Internet 2')

        expense.refresh_from_db()
        self.client.patch(detail_url(expense), {'title': 'Solo este'}, format='json')
        self.assertEqual(RecurringExpense.objects.get().template['title'], 'Internet 2')

        self.client.patch(f'{detail_url(expense)}?scope=following', {'repeat': None}, format='json')
        series = RecurringExpense.objects.get()
        self.assertFalse(series.active)
        self.assertIsNone(self.client.get(detail_url(expense)).data['repeat'])

    def test_delete_following_ends_the_series(self):
        expense = self.create(DAY, {'frequency': 'monthly'})
        later = create_occurrence(expense.series, date(2026, 12, 20))
        self.as_user(self.member)
        response = self.client.delete(f'{detail_url(expense)}?scope=following')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Expense.objects.filter(pk__in=[expense.pk, later.pk]).exists())
        series = RecurringExpense.objects.get()
        self.assertFalse(series.active)

    def test_delete_only_this_keeps_series(self):
        expense = self.create(DAY, {'frequency': 'monthly'})
        self.as_user(self.member)
        self.client.delete(detail_url(expense))
        self.assertTrue(RecurringExpense.objects.get().active)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReceiptTests(ExpenseTestCase):
    def image(self, name='ticket.jpg', image_format='JPEG', size=(20, 30)):
        buffer = io.BytesIO()
        Image.new('RGB', size, 'white').save(buffer, image_format)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{image_format.lower()}')

    def upload(self, household=None, **kwargs):
        household = household or self.household
        return self.client.post(receipts_url(household), {'image': self.image(**kwargs)}, format='multipart')

    def test_upload_attach_and_download(self):
        self.as_user(self.member)
        response = self.upload()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        receipt_id = response.data['id']

        response = self.client.post(list_url(self.household), self.payload(receipt=receipt_id), format='json')
        self.assertEqual(response.data['receipt'], Receipt.objects.get().pk)

        response = self.client.get(f'{receipts_url(self.household)}{receipt_id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'\xff\xd8'))  # JPEG

        self.as_user(self.outsider)
        self.assertEqual(self.client.get(f'{receipts_url(self.household)}{receipt_id}/').status_code,
                         status.HTTP_404_NOT_FOUND)

    def test_rejects_non_images(self):
        self.as_user(self.member)
        fake = SimpleUploadedFile('ticket.jpg', b'no es una imagen', content_type='image/jpeg')
        response = self.client.post(receipts_url(self.household), {'image': fake}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.upload(name='ticket.gif', image_format='GIF')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_receipt_from_another_household_is_rejected(self):
        self.as_user(self.outsider)
        other_id = self.upload(household=self.other_household).data['id']
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(receipt=other_id), format='json')
        self.assertIn('receipt', response.data)


class ActivityTests(ExpenseTestCase):
    def test_month_mixes_expenses_and_payments(self):
        self.make_expense('Luz', amount='30.00', spent_on=date(2026, 10, 3))
        self.make_expense('Súper', amount='90.00', spent_on=date(2026, 11, 2))
        self.make_expense('Cena', amount='10.50', spent_on=date(2026, 11, 20))
        Payment.objects.create(household=self.household, from_user=self.owner, to_user=self.member,
                               amount=Decimal('5.00'), paid_on=date(2026, 11, 10))
        self.as_user(self.member)

        data = self.client.get(activity_url(self.household)).data
        self.assertEqual(data['month'], '2026-11')
        self.assertEqual(data['total'], '100.50')
        self.assertEqual([i['type'] for i in data['items']], ['expense', 'payment', 'expense'])
        self.assertEqual(data['items'][0]['expense']['title'], 'Cena')
        self.assertEqual(data['previous_month'], '2026-10')

        data = self.client.get(activity_url(self.household), {'month': '2026-10'}).data
        self.assertEqual([i['expense']['title'] for i in data['items']], ['Luz'])
        self.assertIsNone(data['previous_month'])

    def test_empty_and_invalid(self):
        self.as_user(self.member)
        data = self.client.get(activity_url(self.household)).data
        self.assertEqual(data, {'month': None, 'total': '0.00', 'items': [], 'previous_month': None})
        response = self.client.get(activity_url(self.household), {'month': '2026-13'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CurrencyTests(ExpenseTestCase):
    def test_any_member_sets_the_currency(self):
        self.as_user(self.member)
        url = f'/api/v1/households/{self.household.pk}/currency/'
        response = self.client.post(url, {'currency': 'MXN'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['currency'], 'MXN')
        self.assertEqual(self.client.post(url, {'currency': 'BTC'}, format='json').status_code,
                         status.HTTP_400_BAD_REQUEST)
        # No se cambia editando el hogar
        self.as_user(self.owner)
        self.client.patch(f'/api/v1/households/{self.household.pk}/', {'currency': 'EUR'}, format='json')
        self.household.refresh_from_db()
        self.assertEqual(self.household.currency, 'MXN')

        self.as_user(self.outsider)
        self.assertEqual(self.client.post(url, {'currency': 'USD'}, format='json').status_code,
                         status.HTTP_404_NOT_FOUND)
