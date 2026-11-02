from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import INVITE_CODE_ALPHABET, INVITE_CODE_LENGTH, Household, Membership

User = get_user_model()

LIST_URL = '/api/v1/households/'
JOIN_URL = '/api/v1/households/join/'


def detail_url(household):
    return f'/api/v1/households/{household.pk}/'


def member_url(household, user):
    return f'/api/v1/households/{household.pk}/members/{user.pk}/'


class HouseholdTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create(username='uid-owner', email='owner@example.com')
        self.admin = User.objects.create(username='uid-admin', email='admin@example.com')
        self.member = User.objects.create(username='uid-member', email='member@example.com')
        self.outsider = User.objects.create(username='uid-outsider', email='outsider@example.com')

        self.household = Household.objects.create(name='Piso Centro', created_by=self.owner)
        Membership.objects.create(household=self.household, user=self.owner, role=Membership.ROLE_OWNER)
        Membership.objects.create(household=self.household, user=self.admin, role=Membership.ROLE_ADMIN)
        Membership.objects.create(household=self.household, user=self.member, role=Membership.ROLE_MEMBER)

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def role_of(self, user):
        return Membership.objects.get(household=self.household, user=user).role


class AuthenticationTests(HouseholdTestCase):
    def test_requires_authentication(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CreateAndListTests(HouseholdTestCase):
    def test_create_makes_creator_owner(self):
        self.as_user(self.outsider)
        response = self.client.post(LIST_URL, {'name': 'Casa Playa'})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['my_role'], Membership.ROLE_OWNER)
        self.assertEqual(response.data['member_count'], 1)
        household = Household.objects.get(pk=response.data['id'])
        self.assertEqual(household.created_by, self.outsider)
        self.assertEqual(household.get_membership(self.outsider).role, Membership.ROLE_OWNER)

    def test_invite_code_is_generated(self):
        code = self.household.invite_code
        self.assertEqual(len(code), INVITE_CODE_LENGTH)
        self.assertTrue(all(char in INVITE_CODE_ALPHABET for char in code))

    def test_invite_code_cannot_be_set_by_client(self):
        self.as_user(self.outsider)
        response = self.client.post(LIST_URL, {'name': 'Casa', 'invite_code': 'AAAAAAAA'})
        self.assertNotEqual(response.data['invite_code'], 'AAAAAAAA')

    def test_list_only_shows_my_households(self):
        Household.objects.create(name='Ajeno')
        self.as_user(self.member)
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([h['id'] for h in response.data], [self.household.pk])
        self.assertEqual(response.data[0]['member_count'], 3)
        self.assertEqual(response.data[0]['my_role'], Membership.ROLE_MEMBER)


class AccessTests(HouseholdTestCase):
    def test_member_can_retrieve(self):
        self.as_user(self.member)
        response = self.client.get(detail_url(self.household))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_outsider_gets_404(self):
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(detail_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.get(detail_url(self.household) + 'members/').status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_member_cannot_update(self):
        self.as_user(self.member)
        response = self.client.patch(detail_url(self.household), {'name': 'Nuevo'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_update(self):
        self.as_user(self.admin)
        response = self.client.patch(detail_url(self.household), {'name': 'Nuevo'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Nuevo')

    def test_only_owner_can_delete(self):
        self.as_user(self.admin)
        self.assertEqual(self.client.delete(detail_url(self.household)).status_code, status.HTTP_403_FORBIDDEN)

        self.as_user(self.owner)
        self.assertEqual(self.client.delete(detail_url(self.household)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Household.objects.filter(pk=self.household.pk).exists())


class JoinTests(HouseholdTestCase):
    def test_join_with_code(self):
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'invite_code': self.household.invite_code})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['id'], self.household.pk)
        self.assertEqual(response.data['my_role'], Membership.ROLE_MEMBER)
        self.assertEqual(response.data['member_count'], 4)

    def test_join_code_is_case_insensitive(self):
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'invite_code': f'  {self.household.invite_code.lower()} '})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_join_with_invalid_code(self):
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'invite_code': 'NOPE2345'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_join_twice_fails(self):
        self.as_user(self.member)
        response = self.client.post(JOIN_URL, {'invite_code': self.household.invite_code})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.household.memberships.filter(user=self.member).count(), 1)

    def test_regenerate_code(self):
        old_code = self.household.invite_code

        self.as_user(self.member)
        url = detail_url(self.household) + 'regenerate-code/'
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)

        self.as_user(self.admin)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data['invite_code'], old_code)

        # El código antiguo deja de servir
        self.as_user(self.outsider)
        self.assertEqual(
            self.client.post(JOIN_URL, {'invite_code': old_code}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )


class LeaveTests(HouseholdTestCase):
    def leave_url(self):
        return detail_url(self.household) + 'leave/'

    def test_member_can_leave(self):
        self.as_user(self.member)
        self.assertEqual(self.client.post(self.leave_url()).status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNone(self.household.get_membership(self.member))

    def test_owner_cannot_leave_with_other_members(self):
        self.as_user(self.owner)
        self.assertEqual(self.client.post(self.leave_url()).status_code, status.HTTP_400_BAD_REQUEST)

    def test_sole_owner_leaving_deletes_household(self):
        self.household.memberships.exclude(user=self.owner).delete()
        self.as_user(self.owner)
        self.assertEqual(self.client.post(self.leave_url()).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Household.objects.filter(pk=self.household.pk).exists())


class MembersTests(HouseholdTestCase):
    def test_list_members(self):
        self.as_user(self.member)
        response = self.client.get(detail_url(self.household) + 'members/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [(m['email'], m['role']) for m in response.data],
            [('owner@example.com', 'owner'), ('admin@example.com', 'admin'), ('member@example.com', 'member')],
        )

    def test_owner_changes_role(self):
        self.as_user(self.owner)
        response = self.client.patch(member_url(self.household, self.member), {'role': 'admin'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.role_of(self.member), Membership.ROLE_ADMIN)

    def test_admin_cannot_change_roles(self):
        self.as_user(self.admin)
        response = self.client.patch(member_url(self.household, self.member), {'role': 'admin'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.role_of(self.member), Membership.ROLE_MEMBER)

    def test_invalid_role(self):
        self.as_user(self.owner)
        response = self.client.patch(member_url(self.household, self.member), {'role': 'king'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_transfer_ownership(self):
        self.as_user(self.owner)
        response = self.client.patch(member_url(self.household, self.member), {'role': 'owner'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.role_of(self.member), Membership.ROLE_OWNER)
        self.assertEqual(self.role_of(self.owner), Membership.ROLE_ADMIN)
        self.assertEqual(self.household.memberships.filter(role=Membership.ROLE_OWNER).count(), 1)

    def test_cannot_change_own_membership(self):
        self.as_user(self.owner)
        response = self.client.patch(member_url(self.household, self.owner), {'role': 'member'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_remove_member(self):
        self.as_user(self.admin)
        response = self.client.delete(member_url(self.household, self.member))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNone(self.household.get_membership(self.member))

    def test_admin_cannot_remove_admin_or_owner(self):
        other_admin = User.objects.create(username='uid-admin2')
        Membership.objects.create(household=self.household, user=other_admin, role=Membership.ROLE_ADMIN)

        self.as_user(self.admin)
        self.assertEqual(
            self.client.delete(member_url(self.household, other_admin)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(member_url(self.household, self.owner)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_owner_can_remove_admin(self):
        self.as_user(self.owner)
        response = self.client.delete(member_url(self.household, self.admin))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_member_cannot_remove_anyone(self):
        self.as_user(self.member)
        response = self.client.delete(member_url(self.household, self.admin))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unknown_member_returns_404(self):
        self.as_user(self.owner)
        response = self.client.delete(member_url(self.household, self.outsider))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
