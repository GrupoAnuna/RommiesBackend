from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from households.models import Household, Membership

from .models import Task

User = get_user_model()

DUE = datetime(2026, 11, 20, 18, 30, tzinfo=timezone.utc)


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
        self.other_task = Task.objects.create(household=self.other_household, title='Ajena', due_at=DUE)

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def make_task(self, **fields):
        fields.setdefault('title', 'Sacar la basura')
        fields.setdefault('due_at', DUE)
        return Task.objects.create(household=self.household, **fields)


class AccessTests(TaskTestCase):
    def test_requires_authentication(self):
        response = self.client.get(list_url(self.household))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_outsider_gets_404_for_household_and_task(self):
        task = self.make_task()
        self.as_user(self.outsider)

        self.assertEqual(self.client.get(list_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(detail_url(task)).status_code, status.HTTP_404_NOT_FOUND)
        response = self.client.post(list_url(self.household), {'title': 'X', 'due_at': DUE.isoformat()})
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
        response = self.client.post(list_url(self.household), {
            'title': 'Lavar platos',
            'due_at': DUE.isoformat(),
            'assignee': str(self.owner.pk),
            'repeat_rule': Task.REPEAT_WEEKLY,
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['household'], self.household.pk)
        self.assertEqual(response.data['created_by'], self.member.pk)
        self.assertEqual(response.data['assignee_name'], 'Olga')
        self.assertFalse(response.data['is_done'])

    def test_due_at_with_time_is_required(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), {'title': 'Sin fecha'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('due_at', response.data)

        # Sin hora, o sin zona horaria, tampoco vale
        for due_at in ('2026-11-20', '2026-11-20T18:30:00'):
            response = self.client.post(list_url(self.household), {'title': 'Incompleta', 'due_at': due_at})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, due_at)
            self.assertIn('due_at', response.data)

    def test_due_at_keeps_the_offset_instant(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), {
            'title': 'Ciudad de México', 'due_at': '2026-11-20T12:30:00-06:00',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Task.objects.get(pk=response.data['id']).due_at, DUE)

    def test_only_weekly_repeat_is_allowed(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), {
            'title': 'Diaria', 'due_at': DUE.isoformat(), 'repeat_rule': 'daily',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('repeat_rule', response.data)

    def test_cannot_assign_to_non_member(self):
        self.as_user(self.member)
        response = self.client.post(list_url(self.household), {
            'title': 'Barrer', 'due_at': DUE.isoformat(), 'assignee': str(self.outsider.pk),
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('assignee', response.data)

    def test_member_edits_and_unassigns(self):
        task = self.make_task(assignee=self.member)
        self.as_user(self.member)
        response = self.client.patch(detail_url(task), {'title': 'Basura y reciclaje', 'assignee': None}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertEqual(task.title, 'Basura y reciclaje')
        self.assertIsNone(task.assignee)

    def test_household_and_completion_cannot_be_set_by_client(self):
        task = self.make_task()
        self.as_user(self.member)
        self.client.patch(detail_url(task), {
            'household': str(self.other_household.pk),
            'completed_at': DUE.isoformat(),
        })
        task.refresh_from_db()
        self.assertEqual(task.household, self.household)
        self.assertIsNone(task.completed_at)


class DeleteTests(TaskTestCase):
    def test_member_cannot_delete(self):
        task = self.make_task(created_by=self.member)
        self.as_user(self.member)
        response = self.client.delete(detail_url(task))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_admin_and_owner_can_delete(self):
        for user in (self.admin, self.owner):
            task = self.make_task()
            self.as_user(user)
            response = self.client.delete(detail_url(task))
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertFalse(Task.objects.filter(pk=task.pk).exists())


class FilterTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.mine = self.make_task(title='Mía', assignee=self.member, due_at=DUE)
        self.theirs = self.make_task(title='De Olga', assignee=self.owner, due_at=DUE + timedelta(days=2))
        self.free = self.make_task(title='Libre', due_at=DUE + timedelta(days=5))
        self.done = self.make_task(title='Hecha', assignee=self.member, due_at=DUE - timedelta(days=1),
                                   completed_at=DUE, completed_by=self.member)
        self.as_user(self.member)

    def titles(self, **params):
        response = self.client.get(list_url(self.household), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [t['title'] for t in response.data['results']]

    def test_ordered_by_due_date(self):
        self.assertEqual(self.titles(), ['Hecha', 'Mía', 'De Olga', 'Libre'])

    def test_status(self):
        self.assertEqual(self.titles(status='pending'), ['Mía', 'De Olga', 'Libre'])
        self.assertEqual(self.titles(status='done'), ['Hecha'])

    def test_assignee(self):
        self.assertEqual(self.titles(assignee='me'), ['Hecha', 'Mía'])
        self.assertEqual(self.titles(assignee='unassigned'), ['Libre'])
        self.assertEqual(self.titles(assignee=str(self.owner.pk)), ['De Olga'])

    def test_due_range(self):
        titles = self.titles(due_from=DUE.isoformat(), due_to=(DUE + timedelta(days=3)).isoformat())
        self.assertEqual(titles, ['Mía', 'De Olga'])

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

    def test_complete_weekly_creates_next_week(self):
        task = self.make_task(repeat_rule=Task.REPEAT_WEEKLY, assignee=self.member, created_by=self.owner)
        self.as_user(self.member)
        response = self.client.post(action_url(task, 'complete'))

        next_data = response.data['next_task']
        self.assertEqual(str(response.data['task']['next_occurrence']), next_data['id'])
        next_task = Task.objects.get(pk=next_data['id'])
        self.assertEqual(next_task.due_at, DUE + timedelta(weeks=1))
        self.assertEqual(next_task.assignee, self.member)
        self.assertEqual(next_task.repeat_rule, Task.REPEAT_WEEKLY)
        self.assertFalse(next_task.is_done)

    def test_reopen_and_complete_again_does_not_duplicate_next_week(self):
        task = self.make_task(repeat_rule=Task.REPEAT_WEEKLY)
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
