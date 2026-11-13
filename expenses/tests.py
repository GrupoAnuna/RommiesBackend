import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from households.models import Household, Membership

from .models import Expense, ExpenseParticipant

User = get_user_model()

DAY = date(2026, 11, 20)


def list_url(household):
    return f'/api/v1/households/{household.pk}/expenses/'


def detail_url(expense):
    return f'/api/v1/households/{expense.household_id}/expenses/{expense.pk}/'


def export_url(household):
    return f'{list_url(household)}export/'


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

    def make_expense(self, title='Súper', amount='90.00', spent_on=DAY, shares=None, **fields):
        fields.setdefault('paid_by', self.member)
        expense = Expense.objects.create(
            household=self.household, title=title, amount=Decimal(amount), spent_on=spent_on, **fields,
        )
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
        self.assertEqual(response.data['paid_by'], self.member.pk)
        self.assertEqual(response.data['created_by'], self.member.pk)
        self.assertFalse(response.data['is_settled'])
        self.assertEqual({p['user_name'] for p in response.data['participants']}, {'Memo', 'Olga', 'Ana'})
        expense = Expense.objects.get(pk=response.data['id'])
        self.assertIsInstance(expense.amount, Decimal)
        self.assertEqual(expense.participants.count(), 3)

    def test_paid_by_another_member(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(paid_by=str(self.owner.pk)), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['paid_by'], self.owner.pk)
        self.assertEqual(response.data['paid_by_name'], 'Olga')

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
        response = self.client.post(list_url(self.household), self.payload(paid_by=str(self.outsider.pk)), format='json')
        self.assertIn('paid_by', response.data)

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

    def test_settled_expense_cannot_be_edited(self):
        expense = self.make_expense()
        self.as_user(self.member)
        self.client.post(f'{detail_url(expense)}settle/')
        response = self.client.patch(detail_url(expense), {'title': 'Otro'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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
        self.new.settled_at = '2026-11-21T12:00:00Z'
        self.new.save()
        self.as_user(self.member)

    def titles(self, **params):
        response = self.client.get(list_url(self.household), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [e['title'] for e in response.data['results']]

    def test_newest_first(self):
        self.assertEqual(self.titles(), ['Súper', 'Internet', 'Luz'])

    def test_filters(self):
        self.assertEqual(self.titles(status='pending'), ['Internet', 'Luz'])
        self.assertEqual(self.titles(status='settled'), ['Súper'])
        self.assertEqual(self.titles(**{'from': '2026-11-01', 'to': '2026-11-30'}), ['Súper', 'Internet'])

    def test_invalid_filters(self):
        for params in ({'status': 'paid'}, {'from': 'ayer'}, {'from': '2026-11-30', 'to': '2026-11-01'}):
            response = self.client.get(list_url(self.household), params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, params)

    def test_settle_is_idempotent(self):
        response = self.client.post(f'{detail_url(self.mid)}settle/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_settled'])
        first = response.data['settled_at']

        self.as_user(self.owner)
        response = self.client.post(f'{detail_url(self.mid)}settle/')
        self.assertEqual(response.data['settled_at'], first)
        self.assertEqual(response.data['settled_by'], self.member.pk)


class ExportTests(ExpenseTestCase):
    def setUp(self):
        super().setUp()
        self.make_expense('Súper y café', amount='100.00', spent_on=date(2026, 11, 2),
                          shares=[(self.member, '66.67'), (self.owner, '33.33')])
        self.make_expense('=HYPERLINK("http://x")', amount='10.00', spent_on=date(2026, 11, 5))
        self.make_expense('Octubre', spent_on=date(2026, 10, 1))
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
        self.assertEqual(rows[0], ['Fecha', 'Concepto', 'Total', 'Pagó', 'Participante', 'Le toca', 'Estado', 'Liquidado el'])
        self.assertEqual(rows[1], ['2026-11-02', 'Súper y café', '100.00', 'Memo', 'Memo', '66.67', 'Pendiente', ''])
        self.assertEqual(rows[2][4:6], ['Olga', '33.33'])
        self.assertEqual(len(rows), 4)  # Octubre queda fuera

    def test_formulas_are_neutralized(self):
        rows = list(csv.reader(io.StringIO(self.export().content.decode('utf-8-sig'))))
        titles = [row[1] for row in rows[1:]]
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
        for path in (base, base + '{id}/', base + '{id}/settle/', base + 'export/'):
            self.assertIn(path, paths)
