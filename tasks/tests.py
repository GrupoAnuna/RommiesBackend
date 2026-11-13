from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from households.models import Household, Membership

from .models import Task

User = get_user_model()

# Viernes
DUE = date(2026, 11, 20)


def list_url(household):
    return f'/api/v1/households/{household.pk}/tasks/'


def detail_url(task):
    return f'/api/v1/households/{task.household_id}/tasks/{task.pk}/'


def action_url(task, name):
    return f'{detail_url(task)}{name}/'


class TaskTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='owner@example.com', firebase_uid='uid-owner', name='Olga')
        self.admin = User.objects.create_user(email='admin@example.com', firebase_uid='uid-admin')
        self.member = User.objects.create_user(email='member@example.com', firebase_uid='uid-member', name='Memo')
        self.outsider = User.objects.create_user(email='outsider@example.com', firebase_uid='uid-outsider')

        self.household = Household.objects.create(name='Piso Centro', owner=self.owner)
        Membership.objects.create(household=self.household, user=self.owner, role=Membership.ROLE_OWNER)
        Membership.objects.create(household=self.household, user=self.admin, role=Membership.ROLE_ADMIN)
        Membership.objects.create(household=self.household, user=self.member, role=Membership.ROLE_MEMBER)

        # Hogar ajeno con su propia tarea
        self.other_household = Household.objects.create(name='Ajeno', owner=self.outsider)
        Membership.objects.create(household=self.other_household, user=self.outsider, role=Membership.ROLE_OWNER)
        self.other_task = Task.objects.create(household=self.other_household, title='Ajena', due_date=DUE)

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def make_task(self, assignees=(), **fields):
        fields.setdefault('title', 'Sacar la basura')
        fields.setdefault('due_date', DUE)
        task = Task.objects.create(household=self.household, **fields)
        task.assignees.set(assignees or [self.member])
        return task

    def payload(self, **fields):
        data = {'title': 'Lavar platos', 'due_date': DUE.isoformat(), 'assignees': [str(self.member.pk)]}
        data.update(fields)
        return data


class AccessTests(TaskTestCase):
    def test_requires_authentication(self):
        response = self.client.get(list_url(self.household))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_outsider_gets_404_for_household_and_task(self):
        task = self.make_task()
        self.as_user(self.outsider)

        self.assertEqual(self.client.get(list_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(detail_url(task)).status_code, status.HTTP_404_NOT_FOUND)
        response = self.client.post(list_url(self.household), self.payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_task_from_another_household_is_not_reachable(self):
        # Mi hogar en la URL, tarea de otro hogar: no existe
        self.as_user(self.member)
        url = f'/api/v1/households/{self.household.pk}/tasks/{self.other_task.pk}/'
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_list_only_shows_tasks_of_the_household(self):
        task = self.make_task()
        self.as_user(self.member)
        response = self.client.get(list_url(self.household))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(task.pk))


class CreateAndEditTests(TaskTestCase):
    def test_member_creates_task(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(
            assignees=[str(self.member.pk), str(self.owner.pk)],
            reminder_time='20:00',
            repeat_days=[3, 0, 3],
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['household'], self.household.pk)
        self.assertEqual(response.data['created_by'], self.member.pk)
        self.assertEqual(set(response.data['assignees']), {self.member.pk, self.owner.pk})
        self.assertEqual({a['name'] for a in response.data['assignee_details']}, {'Memo', 'Olga'})
        self.assertEqual(response.data['reminder_time'], '20:00')
        self.assertEqual(response.data['repeat_days'], [0, 3])
        self.assertFalse(response.data['is_done'])

    def test_reminder_and_repeat_are_optional(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['reminder_time'])
        self.assertEqual(response.data['repeat_days'], [])

    def test_due_date_is_required(self):
        self.as_user(self.member)
        data = self.payload()
        del data['due_date']
        response = self.client.post(list_url(self.household), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('due_date', response.data)

    def test_at_least_one_assignee(self):
        self.as_user(self.member)
        empty = self.payload(assignees=[])
        missing = self.payload()
        del missing['assignees']
        for data in (empty, missing):
            response = self.client.post(list_url(self.household), data, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('assignees', response.data)

    def test_invalid_repeat_days(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), self.payload(repeat_days=[7]), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('repeat_days', response.data)

    def test_cannot_assign_to_non_member(self):
        self.as_user(self.member)
        response = self.client.post(
            list_url(self.household),
            self.payload(assignees=[str(self.member.pk), str(self.outsider.pk)]),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('assignees', response.data)

    def test_member_edits_assignees_and_reminder(self):
        task = self.make_task(reminder_time=time(20, 0))
        self.as_user(self.member)
        response = self.client.patch(detail_url(task), {
            'title': 'Basura y reciclaje',
            'assignees': [str(self.owner.pk), str(self.admin.pk)],
            'reminder_time': None,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertEqual(task.title, 'Basura y reciclaje')
        self.assertEqual(set(task.assignees.all()), {self.owner, self.admin})
        self.assertIsNone(task.reminder_time)

    def test_patch_without_assignees_keeps_them(self):
        task = self.make_task(assignees=[self.owner])
        self.as_user(self.member)
        self.client.patch(detail_url(task), {'title': 'Otro'}, format='json')
        self.assertEqual(list(task.assignees.all()), [self.owner])

    def test_household_and_completion_cannot_be_set_by_client(self):
        task = self.make_task()
        self.as_user(self.member)
        self.client.patch(detail_url(task), {
            'household': str(self.other_household.pk),
            'completed_at': '2026-11-20T10:00:00Z',
        }, format='json')
        task.refresh_from_db()
        self.assertEqual(task.household, self.household)
        self.assertIsNone(task.completed_at)


class DeleteTests(TaskTestCase):
    def test_member_cannot_delete_someone_elses_task(self):
        task = self.make_task(created_by=self.owner)
        self.as_user(self.member)
        response = self.client.delete(detail_url(task))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_creator_can_delete(self):
        task = self.make_task(created_by=self.member)
        self.as_user(self.member)
        response = self.client.delete(detail_url(task))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_admin_and_owner_can_delete(self):
        for user in (self.admin, self.owner):
            task = self.make_task(created_by=self.member)
            self.as_user(user)
            response = self.client.delete(detail_url(task))
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertFalse(Task.objects.filter(pk=task.pk).exists())


class FilterTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.make_task(title='Mía', assignees=[self.member], due_date=DUE)
        self.make_task(title='Compartida', assignees=[self.member, self.owner], due_date=DUE + timedelta(days=1))
        self.make_task(title='De Olga', assignees=[self.owner], due_date=DUE + timedelta(days=2))
        self.make_task(title='Hecha', due_date=DUE - timedelta(days=1),
                       completed_at='2026-11-19T10:00:00Z', completed_by=self.member)
        self.as_user(self.member)

    def titles(self, **params):
        response = self.client.get(list_url(self.household), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [t['title'] for t in response.data['results']]

    def test_ordered_by_due_date(self):
        self.assertEqual(self.titles(), ['Hecha', 'Mía', 'Compartida', 'De Olga'])

    def test_status(self):
        self.assertEqual(self.titles(status='pending'), ['Mía', 'Compartida', 'De Olga'])
        self.assertEqual(self.titles(status='done'), ['Hecha'])

    def test_assignee(self):
        self.assertEqual(self.titles(assignee='me', status='pending'), ['Mía', 'Compartida'])
        self.assertEqual(self.titles(assignee=str(self.owner.pk)), ['Compartida', 'De Olga'])

    def test_due_range(self):
        titles = self.titles(due_from=DUE.isoformat(), due_to=(DUE + timedelta(days=1)).isoformat())
        self.assertEqual(titles, ['Mía', 'Compartida'])

    def test_invalid_filters_return_400(self):
        for params in ({'status': 'later'}, {'assignee': 'nadie'}, {'due_from': 'mañana'}):
            response = self.client.get(list_url(self.household), params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, params)


class CompleteTests(TaskTestCase):
    def test_complete_one_off_task(self):
        task = self.make_task()
        self.as_user(self.member)
        response = self.client.post(action_url(task, 'complete'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['task']['is_done'])
        self.assertEqual(response.data['task']['completed_by'], self.member.pk)
        self.assertIsNone(response.data['next_task'])
        self.assertEqual(Task.objects.filter(household=self.household).count(), 1)

    def test_complete_repeating_task_creates_next_marked_day(self):
        # Viernes 20, se repite lunes y jueves → la siguiente vence el lunes 23
        task = self.make_task(repeat_days=[0, 3], reminder_time=time(20, 0),
                              assignees=[self.member, self.owner], created_by=self.owner)
        self.as_user(self.member)
        response = self.client.post(action_url(task, 'complete'))

        next_data = response.data['next_task']
        self.assertEqual(str(response.data['task']['next_occurrence']), next_data['id'])
        next_task = Task.objects.get(pk=next_data['id'])
        self.assertEqual(next_task.due_date, date(2026, 11, 23))
        self.assertEqual(next_task.reminder_time, time(20, 0))
        self.assertEqual(set(next_task.assignees.all()), {self.member, self.owner})
        self.assertEqual(next_task.repeat_days, [0, 3])
        self.assertFalse(next_task.is_done)

    def test_same_weekday_repeats_next_week(self):
        task = self.make_task(repeat_days=[DUE.weekday()])
        self.as_user(self.member)
        next_id = self.client.post(action_url(task, 'complete')).data['next_task']['id']
        self.assertEqual(Task.objects.get(pk=next_id).due_date, DUE + timedelta(weeks=1))

    def test_reopen_and_complete_again_does_not_duplicate_next(self):
        task = self.make_task(repeat_days=[0])
        self.as_user(self.member)

        first = self.client.post(action_url(task, 'complete')).data['next_task']['id']
        response = self.client.post(action_url(task, 'reopen'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_done'])
        self.assertIsNone(response.data['completed_by'])

        second = self.client.post(action_url(task, 'complete')).data['next_task']['id']
        self.assertEqual(first, second)
        self.assertEqual(Task.objects.filter(household=self.household).count(), 2)

    def test_completing_twice_keeps_first_completion(self):
        task = self.make_task()
        self.as_user(self.member)
        self.client.post(action_url(task, 'complete'))
        self.as_user(self.owner)
        response = self.client.post(action_url(task, 'complete'))
        self.assertEqual(response.data['task']['completed_by'], self.member.pk)

    def test_outsider_cannot_complete(self):
        task = self.make_task()
        self.as_user(self.outsider)
        response = self.client.post(action_url(task, 'complete'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SchemaTests(APITestCase):
    def test_openapi_documents_task_endpoints(self):
        response = self.client.get('/api/schema/', {'format': 'json'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        paths = response.json()['paths']
        base = '/api/v1/households/{household_pk}/tasks/'
        for path in (base, base + '{id}/', base + '{id}/complete/', base + '{id}/reopen/'):
            self.assertIn(path, paths)
