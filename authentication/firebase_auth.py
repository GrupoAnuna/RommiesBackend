from rest_framework import authentication, exceptions
from firebase_admin import auth as firebase_auth
from django.contrib.auth import get_user_model

User = get_user_model()

class FirebaseAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')

        if not auth_header:
            return None

        try:
            prefix, token = auth_header.split(' ')
        except ValueError:
            raise exceptions.AuthenticationFailed('Invalid Authorization header')

        if prefix.lower() != 'bearer':
            return None

        try:
            decoded_token = firebase_auth.verify_id_token(token)
        except Exception:
            raise exceptions.AuthenticationFailed('Invalid Firebase token')

        uid = decoded_token.get('uid')
        email = decoded_token.get('email')

        if not uid:
            raise exceptions.AuthenticationFailed('Firebase token has no uid')

        user, created = User.objects.get_or_create(
            username=uid,
            defaults={'email': email or ''}
        )

        return (user, None)
