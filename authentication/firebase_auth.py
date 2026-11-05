from rest_framework import authentication, exceptions
from firebase_admin import auth as firebase_auth
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from drf_spectacular.extensions import OpenApiAuthenticationExtension

User = get_user_model()


def get_or_create_firebase_user(decoded_token):
    uid = decoded_token['uid']
    email = (decoded_token.get('email') or '').strip().lower()

    user = User.objects.filter(firebase_uid=uid).first()
    if user:
        return user

    if not email:
        raise exceptions.AuthenticationFailed('Firebase token has no email')

    existing = User.objects.filter(email__iexact=email).first()
    if existing:
        # Firebase garantiza un correo por cuenta: si está verificado, la cuenta
        # se recreó en Firebase (nuevo UID) y se vuelve a vincular al usuario existente
        if not decoded_token.get('email_verified'):
            raise exceptions.AuthenticationFailed('Email already registered')
        existing.firebase_uid = uid
        existing.save(update_fields=['firebase_uid'])
        return existing

    try:
        with transaction.atomic():
            return User.objects.create_user(
                email=email,
                firebase_uid=uid,
                name=decoded_token.get('name') or '',
            )
    except IntegrityError:
        # Dos peticiones simultáneas del primer login: la otra ya lo creó
        return User.objects.get(firebase_uid=uid)


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

        if not decoded_token.get('uid'):
            raise exceptions.AuthenticationFailed('Firebase token has no uid')

        user = get_or_create_firebase_user(decoded_token)
        if not user.is_active:
            raise exceptions.AuthenticationFailed('User is inactive')

        return (user, None)

    def authenticate_header(self, request):
        # Permite a DRF responder 401 (en vez de 403) cuando falta el token
        return 'Bearer'


class FirebaseAuthenticationScheme(OpenApiAuthenticationExtension):
    # Documenta en Swagger el header "Authorization: Bearer <Firebase ID token>"
    target_class = 'authentication.firebase_auth.FirebaseAuthentication'
    name = 'FirebaseAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'Firebase ID token',
        }
