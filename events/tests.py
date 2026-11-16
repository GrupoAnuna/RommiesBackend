from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from expenses.models import REPEAT_MONTHLY as EXPENSE_MONTHLY
from expenses.models import RecurringExpense
from households.models import Household, Membership
from tasks.models import Task

from .models import REPEAT_MONTHLY, REPEAT_WEEKLY, REPEAT_YEARLY, Event
from .occurrences import occurrences

User = get_user_model()

# Lunes
DAY = date(2026, 11, 2)


def list_url(household):
    return f'/api/v1/households/{household.pk}/events/'


def detail_url(event, **params):
    url = f'/api/v1/households/{event.household_id}/events/{event.pk}/'
    if params:
        url += '?' + '&'.join(f'{key}={value}' for key, value in params.items())
    return url


def calendar_url(household, start, end):
    return f'/api/v1/households/{household.pk}/calendar/?from={start.isoformat()}&to={end.isoformat()}'


class EventTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='owner@example.com', firebase_uid='uid-owner', name='Sofía')
        self.admin = User.objects.create_user(email='admin@example.com', firebase_uid='uid-admin', name='Tomás')
        self.member = User.objects.create_user(email='member@example.com', firebase_uid='uid-member', name='Mica')
        self.outsider = User.objects.create_user(email='outsider@example.com', firebase_uid='uid-outsider')

        self.household = Household.objects.create(name='Apto 4B', owner=self.owner)
        Membership.objects.create(household=self.household, user=self.owner, role=Membership.ROLE_OWNER)
        Membership.objects.create(household=self.household, user=self.admin, role=Membership.ROLE_ADMIN)
        Membership.objects.create(household=self.household, user=self.member, role=Membership.ROLE_MEMBER)

        self.other_household = Household.objects.create(name='Ajeno', owner=self.outsider)
        Membership.objects.create(household=self.other_household, user=self.outsider, role=Membership.ROLE_OWNER)

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def make_event(self, participants=None, **fields):
        fields.setdefault('title', 'Reunión del hogar')
        fields.setdefault('date', DAY)
        fields.setdefault('start_time', time(20, 30))
        fields.setdefault('end_time', time(21, 30))
        fields.setdefault('created_by', self.member)
        event = Event.objects.create(household=self.household, **fields)
        event.participants.set(participants or [self.owner, self.admin, self.member])
        return event

    def payload(self, **fields):
        data = {
            'title': 'Reunión del hogar',
            'participants': [str(self.owner.pk), str(self.member.pk)],
            'date': DAY.isoformat(),
            'start_time': '20:30',
            'end_time': '21:30',
        }
        data.update(fields)
        return data

    def agenda(self, start, end):
        response = self.client.get(calendar_url(self.household, start, end))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data


class OccurrencesTests(EventTestCase):
    def days(self, event, start, end):
        return list(occurrences(event, start, end))

    def test_single_event(self):
        event = self.make_event()
        self.assertEqual(self.days(event, date(2026, 11, 1), date(2026, 11, 30)), [DAY])
        self.assertEqual(self.days(event, date(2026, 11, 3), date(2026, 11, 30)), [])

    def test_weekly_on_marked_days_until_end(self):
        event = self.make_event(
            repeat_frequency=REPEAT_WEEKLY, repeat_weekdays=[0, 3], repeat_until=date(2026, 11, 12),
        )
        self.assertEqual(
            self.days(event, date(2026, 10, 1), date(2026, 11, 30)),
            [date(2026, 11, 2), date(2026, 11, 5), date(2026, 11, 9), date(2026, 11, 12)],
        )

    def test_monthly_keeps_day_and_clamps_short_months(self):
        event = self.make_event(date=date(2027, 1, 31), repeat_frequency=REPEAT_MONTHLY)
        self.assertEqual(
            self.days(event, date(2027, 1, 1), date(2027, 4, 30)),
            [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)],
        )

    def test_yearly(self):
        event = self.make_event(date=date(2026, 11, 7), repeat_frequency=REPEAT_YEARLY)
        self.assertEqual(
            self.days(event, date(2026, 1, 1), date(2028, 12, 31)),
            [date(2026, 11, 7), date(2027, 11, 7), date(2028, 11, 7)],
        )

    def test_excluded_dates_are_skipped(self):
        event = self.make_event(repeat_frequency=REPEAT_WEEKLY, excluded_dates=['2026-11-09'])
        self.assertEqual(
            self.days(event, DAY, date(2026, 11, 16)),
            [date(2026, 11, 2), date(2026, 11, 16)],
        )


class EventCrudTests(EventTestCase):
    def test_member_creates_event(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(reminder_minutes=30), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['start_time'], '20:30')
        self.assertEqual(response.data['reminder_minutes'], 30)
        self.assertFalse(response.data['everyone'])
        self.assertEqual(response.data['created_by_name'], 'Mica')
        event = Event.objects.get(pk=response.data['id'])
        self.assertEqual(event.created_by, self.member)
        self.assertEqual(set(event.participants.all()), {self.owner, self.member})

    def test_everyone_when_all_members_participate(self):
        self.as_user(self.member)
        members = [str(user.pk) for user in (self.owner, self.admin, self.member)]
        response = self.client.post(list_url(self.household), self.payload(participants=members), format='json')
        self.assertTrue(response.data['everyone'])

    def test_validation_errors(self):
        self.as_user(self.member)
        cases = [
            ({'title': ''}, 'title'),
            ({'participants': []}, 'participants'),
            ({'participants': [str(self.outsider.pk)]}, 'participants'),
            ({'end_time': '20:00'}, 'end_time'),
            ({'start_time': None}, 'start_time'),
            ({'reminder_minutes': 15}, 'reminder_minutes'),
            ({'repeat_frequency': REPEAT_MONTHLY, 'repeat_until': DAY.isoformat()}, 'repeat_until'),
        ]
        for fields, field in cases:
            with self.subTest(field=field, fields=fields):
                response = self.client.post(list_url(self.household), self.payload(**fields), format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_all_day_clears_times(self):
        self.as_user(self.member)
        response = self.client.post(
            list_url(self.household), self.payload(all_day=True, start_time=None, end_time=None), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['start_time'])

    def test_weekly_without_days_uses_event_weekday(self):
        self.as_user(self.member)
        response = self.client.post(
            list_url(self.household), self.payload(repeat_frequency=REPEAT_WEEKLY), format='json',
        )
        self.assertEqual(response.data['repeat_weekdays'], [0])

    def test_non_weekly_drops_weekdays(self):
        self.as_user(self.member)
        response = self.client.post(
            list_url(self.household),
            self.payload(repeat_frequency=REPEAT_YEARLY, repeat_weekdays=[1, 2]),
            format='json',
        )
        self.assertEqual(response.data['repeat_weekdays'], [])

    def test_outsider_gets_404(self):
        event = self.make_event()
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(list_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(detail_url(event)).status_code, status.HTTP_404_NOT_FOUND)

    def test_list_filters_by_date(self):
        self.make_event()
        self.make_event(title='Otra', date=date(2026, 12, 1))
        self.as_user(self.member)
        response = self.client.get(list_url(self.household) + '?date_to=2026-11-30')
        self.assertEqual([item['title'] for item in response.data['results']], ['Reunión del hogar'])

    def test_any_member_edits(self):
        event = self.make_event()
        self.as_user(self.owner)
        response = self.client.patch(detail_url(event), {'title': 'Cena'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Cena')

    def test_patch_end_before_existing_start_fails(self):
        event = self.make_event()
        self.as_user(self.member)
        response = self.client.patch(detail_url(event), {'end_time': '19:00'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_creator_or_admin_deletes(self):
        event = self.make_event(created_by=self.owner)
        self.as_user(self.member)
        self.assertEqual(self.client.delete(detail_url(event)).status_code, status.HTTP_403_FORBIDDEN)
        self.as_user(self.admin)
        self.assertEqual(self.client.delete(detail_url(event)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())


class RecurringEditTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.series = self.make_event(repeat_frequency=REPEAT_WEEKLY, repeat_weekdays=[0])
        self.as_user(self.member)

    def test_scope_needs_occurrence(self):
        response = self.client.patch(detail_url(self.series, scope='this'), {'title': 'X'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('occurrence', response.data)

    def test_occurrence_must_be_a_day_of_the_series(self):
        response = self.client.patch(
            detail_url(self.series, scope='this', occurrence='2026-11-03'), {'title': 'X'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_edit_only_this_day(self):
        response = self.client.patch(
            detail_url(self.series, scope='this', occurrence='2026-11-09'), {'title': 'Especial'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['title'], 'Especial')
        self.assertEqual(response.data['date'], '2026-11-09')
        self.assertEqual(response.data['repeat_frequency'], '')
        self.assertEqual(response.data['series'], self.series.pk)
        self.assertEqual(len(response.data['participants']), 3)

        self.series.refresh_from_db()
        self.assertEqual(self.series.excluded_dates, ['2026-11-09'])
        agenda = self.agenda(DAY, date(2026, 11, 16))
        self.assertEqual(
            [(item['date'], item['title']) for item in agenda['items']],
            [('2026-11-02', 'Reunión del hogar'), ('2026-11-09', 'Especial'), ('2026-11-16', 'Reunión del hogar')],
        )

    def test_edit_only_this_day_ignores_repeat_rule(self):
        response = self.client.patch(
            detail_url(self.series, scope='this', occurrence='2026-11-09'),
            {'title': 'Especial', 'repeat_frequency': REPEAT_WEEKLY, 'repeat_weekdays': [0]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['repeat_frequency'], '')

    def test_edit_this_and_following(self):
        response = self.client.patch(
            detail_url(self.series, scope='following', occurrence='2026-11-16'),
            {'start_time': '19:00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['repeat_frequency'], REPEAT_WEEKLY)
        self.assertEqual(response.data['date'], '2026-11-16')

        self.series.refresh_from_db()
        self.assertEqual(self.series.repeat_until, date(2026, 11, 15))
        agenda = self.agenda(DAY, date(2026, 11, 23))
        self.assertEqual(
            [(item['date'], item['start_time']) for item in agenda['items']],
            [('2026-11-02', '20:30'), ('2026-11-09', '20:30'), ('2026-11-16', '19:00'), ('2026-11-23', '19:00')],
        )

    def test_following_from_first_day_edits_whole_series(self):
        response = self.client.patch(
            detail_url(self.series, scope='following', occurrence=DAY.isoformat()), {'title': 'Todo'}, format='json',
        )
        self.assertEqual(response.data['id'], str(self.series.pk))
        self.assertEqual(Event.objects.count(), 1)

    def test_following_moves_later_overrides_to_new_series(self):
        self.client.patch(detail_url(self.series, scope='this', occurrence='2026-11-23'), {'title': 'Suelta'}, format='json')
        response = self.client.patch(
            detail_url(self.series, scope='following', occurrence='2026-11-16'), {'title': 'Nueva'}, format='json',
        )
        new_series = Event.objects.get(pk=response.data['id'])
        self.assertEqual(new_series.excluded_dates, ['2026-11-23'])
        self.assertEqual(Event.objects.get(title='Suelta').series, new_series)
        self.series.refresh_from_db()
        self.assertEqual(self.series.excluded_dates, [])

    def test_delete_only_this_day(self):
        response = self.client.delete(detail_url(self.series, scope='this', occurrence='2026-11-09'))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.series.refresh_from_db()
        self.assertEqual(self.series.excluded_dates, ['2026-11-09'])

    def test_delete_this_and_following(self):
        self.client.patch(detail_url(self.series, scope='this', occurrence='2026-11-23'), {'title': 'Suelta'}, format='json')
        response = self.client.delete(detail_url(self.series, scope='following', occurrence='2026-11-16'))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.series.refresh_from_db()
        self.assertEqual(self.series.repeat_until, date(2026, 11, 15))
        self.assertFalse(Event.objects.filter(title='Suelta').exists())

    def test_delete_whole_series_removes_overrides(self):
        self.client.patch(detail_url(self.series, scope='this', occurrence='2026-11-09'), {'title': 'Suelta'}, format='json')
        self.client.delete(detail_url(self.series))
        self.assertEqual(Event.objects.count(), 0)


class CalendarTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.as_user(self.member)

    def test_range_is_required_and_limited(self):
        url = f'/api/v1/households/{self.household.pk}/calendar/'
        self.assertEqual(self.client.get(url).status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.get(calendar_url(self.household, DAY, date(2027, 3, 1)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.get(calendar_url(self.household, DAY, date(2026, 11, 1)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_outsider_gets_404(self):
        self.as_user(self.outsider)
        response = self.client.get(calendar_url(self.household, DAY, DAY))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_mixes_events_tasks_and_expenses_in_design_order(self):
        self.make_event(title='Reunión del hogar', reminder_minutes=30)
        self.make_event(title='Desayuno', start_time=time(9), end_time=time(10))
        self.make_event(title='Feriado', all_day=True, start_time=None, end_time=None)
        task = Task.objects.create(household=self.household, title='Sacar la basura', due_date=DAY, repeat_days=[0])
        task.assignees.set([self.member])
        Task.objects.create(household=self.household, title='Hecha', due_date=DAY, completed_at='2026-11-01T10:00Z')
        RecurringExpense.objects.create(
            household=self.household,
            frequency=EXPENSE_MONTHLY,
            day_of_month=2,
            starts_on=date(2026, 10, 2),
            last_date=date(2026, 10, 2),
            template={
                'title': 'Internet',
                'amount': '18500.00',
                'payers': [{'user': str(self.owner.pk), 'amount': '18500.00'}],
                'participants': [],
            },
        )

        agenda = self.agenda(DAY, DAY)

        rows = [(item['type'], item['title']) for item in agenda['items']]
        self.assertEqual(rows, [
            ('event', 'Feriado'),
            ('task', 'Sacar la basura'),
            ('expense', 'Internet'),
            ('event', 'Desayuno'),
            ('event', 'Reunión del hogar'),
        ])
        expense = agenda['items'][2]
        self.assertEqual(Decimal(expense['amount']), Decimal('18500.00'))
        self.assertEqual([person['name'] for person in expense['people']], ['Sofía'])
        self.assertTrue(expense['recurring'])
        # El 2 nov ya pasó: el gasto de la serie ya se creó
        self.assertFalse(expense['scheduled'])
        self.assertIsNotNone(expense['expense_id'])
        self.assertTrue(agenda['items'][0]['everyone'])
        self.assertEqual(agenda['items'][4]['reminder_minutes'], 30)
        self.assertFalse(agenda['items'][1]['scheduled'])
        self.assertEqual(agenda['busy_days'], [DAY.isoformat()])

    def test_recurring_task_projects_next_days(self):
        task = Task.objects.create(household=self.household, title='Basura', due_date=DAY, repeat_days=[0, 3])
        task.assignees.set([self.member])
        agenda = self.agenda(DAY, date(2026, 11, 9))
        self.assertEqual(
            [item['date'] for item in agenda['items']],
            ['2026-11-02', '2026-11-05', '2026-11-09'],
        )

    def test_future_expense_is_scheduled(self):
        RecurringExpense.objects.create(
            household=self.household,
            frequency=EXPENSE_MONTHLY,
            day_of_month=2,
            starts_on=date(2026, 10, 2),
            last_date=date(2026, 10, 2),
            template={'title': 'Internet', 'amount': '100.00', 'payers': [], 'participants': []},
        )
        agenda = self.agenda(date(2027, 1, 1), date(2027, 1, 31))
        self.assertEqual([(item['date'], item['scheduled']) for item in agenda['items']], [('2027-01-02', True)])

    def test_busy_days_for_month_grid(self):
        self.make_event(repeat_frequency=REPEAT_WEEKLY, repeat_weekdays=[0], repeat_until=date(2026, 11, 16))
        agenda = self.agenda(date(2026, 10, 26), date(2026, 12, 6))
        self.assertEqual(agenda['busy_days'], ['2026-11-02', '2026-11-09', '2026-11-16'])
