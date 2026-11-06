from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import JOIN_CODE_ALPHABET, JOIN_CODE_LENGTH, Household, Membership

User = get_user_model()

LIST_URL = '/api/v1/households/'
JOIN_URL = '/api/v1/households/join/'


def detail_url(household):
    return f'/api/v1/households/{household.pk}/'


def member_url(household, user):
    return f'/api/v1/households/{household.pk}/members/{user.pk}/'


class HouseholdTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='owner@example.com', firebase_uid='uid-owner')
        self.admin = User.objects.create_user(email='admin@example.com', firebase_uid='uid-admin')
        self.member = User.objects.create_user(email='member@example.com', firebase_uid='uid-member')
        self.outsider = User.objects.create_user(email='outsider@example.com', firebase_uid='uid-outsider')

        self.household = Household.objects.create(name='Piso Centro', owner=self.owner)
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
        self.assertEqual(household.owner, self.outsider)
        self.assertEqual(household.get_membership(self.outsider).role, Membership.ROLE_OWNER)

    def test_join_code_is_generated(self):
        code = self.household.join_code
        self.assertEqual(len(code), JOIN_CODE_LENGTH)
        self.assertTrue(all(char in JOIN_CODE_ALPHABET for char in code))

    def test_join_code_and_owner_cannot_be_set_by_client(self):
        self.as_user(self.outsider)
        response = self.client.post(LIST_URL, {'name': 'Casa', 'join_code': 'AAAAAAAA', 'owner': self.owner.pk})
        self.assertNotEqual(response.data['join_code'], 'AAAAAAAA')
        self.assertEqual(response.data['owner'], self.outsider.pk)

    def test_list_only_shows_my_households(self):
        Household.objects.create(name='Ajeno', owner=self.outsider)
        self.as_user(self.member)
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([h['id'] for h in response.data], [str(self.household.pk)])
        self.assertEqual(response.data[0]['member_count'], 3)
        self.assertEqual(response.data[0]['my_role'], Membership.ROLE_MEMBER)


class AccessTests(HouseholdTestCase):
    def test_invalid_id_returns_404(self):
        self.as_user(self.member)
        response = self.client.get('/api/v1/households/not-a-uuid/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_invited_user_is_not_a_member_yet(self):
        Membership.objects.create(
            household=self.household, user=self.outsider, status=Membership.STATUS_INVITED,
        )
        self.as_user(self.outsider)
        self.assertEqual(self.client.get(detail_url(self.household)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(LIST_URL).data, [])

        self.as_user(self.member)
        self.assertEqual(self.client.get(detail_url(self.household)).data['member_count'], 3)

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
        response = self.client.post(JOIN_URL, {'join_code': self.household.join_code})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['id'], str(self.household.pk))
        self.assertEqual(response.data['my_role'], Membership.ROLE_MEMBER)
        self.assertEqual(response.data['member_count'], 4)

    def test_join_code_is_case_insensitive(self):
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'join_code': f'  {self.household.join_code.lower()} '})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_join_with_invalid_code(self):
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'join_code': 'NOPE2345'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_join_twice_fails(self):
        self.as_user(self.member)
        response = self.client.post(JOIN_URL, {'join_code': self.household.join_code})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.household.memberships.filter(user=self.member).count(), 1)

    def test_join_accepts_pending_invitation(self):
        Membership.objects.create(
            household=self.household, user=self.outsider, status=Membership.STATUS_INVITED,
        )
        self.as_user(self.outsider)
        response = self.client.post(JOIN_URL, {'join_code': self.household.join_code})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membership = Membership.objects.get(household=self.household, user=self.outsider)
        self.assertEqual(membership.status, Membership.STATUS_ACTIVE)

    def test_regenerate_code(self):
        old_code = self.household.join_code

        self.as_user(self.member)
        url = detail_url(self.household) + 'regenerate-code/'
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)

        self.as_user(self.admin)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data['join_code'], old_code)

        # El código antiguo deja de servir
        self.as_user(self.outsider)
        self.assertEqual(
            self.client.post(JOIN_URL, {'join_code': old_code}).status_code,
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
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.member)

    def test_cannot_transfer_ownership_to_invited_user(self):
        Membership.objects.create(
            household=self.household, user=self.outsider, status=Membership.STATUS_INVITED,
        )
        self.as_user(self.owner)
        response = self.client.patch(member_url(self.household, self.outsider), {'role': 'owner'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.owner)

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
        other_admin = User.objects.create_user(email='admin2@example.com', firebase_uid='uid-admin2')
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
