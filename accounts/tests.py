from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from households.models import Household, Membership

from .models import User

ME_URL = '/api/v1/me/'
VERIFY_TOKEN = 'authentication.firebase_auth.firebase_auth.verify_id_token'


class MeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ana@example.com', firebase_uid='uid-ana', name='Ana')
        self.client.force_authenticate(user=self.user)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(ME_URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_new_user_has_no_households(self):
        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'ana@example.com')
        self.assertEqual(response.data['households'], [])

    def test_lists_active_households_only(self):
        home = Household.objects.create(name='Piso Centro', owner=self.user)
        Membership.objects.create(household=home, user=self.user, role=Membership.ROLE_OWNER)
        other_owner = User.objects.create_user(email='otro@example.com', firebase_uid='uid-otro')
        pending = Household.objects.create(name='Pendiente', owner=other_owner)
        Membership.objects.create(household=pending, user=self.user, status=Membership.STATUS_INVITED)

        response = self.client.get(ME_URL)
        self.assertEqual(
            response.data['households'],
            [{'id': str(home.pk), 'name': 'Piso Centro', 'role': 'owner'}],
        )

    def test_update_name_and_locale(self):
        response = self.client.patch(ME_URL, {'name': 'Ana María', 'locale': 'en-US', 'email': 'x@x.com'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual((self.user.name, self.user.locale), ('Ana María', 'en-US'))
        self.assertEqual(self.user.email, 'ana@example.com')


@patch(VERIFY_TOKEN)
class FirebaseAuthenticationTests(APITestCase):
    def get_me(self):
        return self.client.get(ME_URL, HTTP_AUTHORIZATION='Bearer token')

    def test_first_request_creates_user(self, verify):
        verify.return_value = {'uid': 'uid-new', 'email': 'New@Example.com', 'name': 'Nuevo'}

        response = self.get_me()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user = User.objects.get(firebase_uid='uid-new')
        self.assertEqual((user.email, user.name), ('new@example.com', 'Nuevo'))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(response.data['households'], [])

    def test_existing_user_is_reused(self, verify):
        user = User.objects.create_user(email='ana@example.com', firebase_uid='uid-ana')
        verify.return_value = {'uid': 'uid-ana', 'email': 'ana@example.com'}

        self.get_me()
        self.get_me()

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.get_me().data['id'], str(user.pk))

    def test_recreated_firebase_account_is_relinked_by_verified_email(self, verify):
        user = User.objects.create_user(email='ana@example.com', firebase_uid='uid-old')
        verify.return_value = {'uid': 'uid-new', 'email': 'ana@example.com', 'email_verified': True}

        self.assertEqual(self.get_me().status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.firebase_uid, 'uid-new')
        self.assertEqual(User.objects.count(), 1)

    def test_unverified_email_cannot_take_over_existing_user(self, verify):
        User.objects.create_user(email='ana@example.com', firebase_uid='uid-old')
        verify.return_value = {'uid': 'uid-new', 'email': 'ana@example.com', 'email_verified': False}

        self.assertEqual(self.get_me().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_without_email_is_rejected(self, verify):
        verify.return_value = {'uid': 'uid-phone'}
        self.assertEqual(self.get_me().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token(self, verify):
        verify.side_effect = ValueError('bad token')
        self.assertEqual(self.get_me().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_is_rejected(self, verify):
        User.objects.create_user(email='ana@example.com', firebase_uid='uid-ana', is_active=False)
        verify.return_value = {'uid': 'uid-ana', 'email': 'ana@example.com'}
        self.assertEqual(self.get_me().status_code, status.HTTP_401_UNAUTHORIZED)
