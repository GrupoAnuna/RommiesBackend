import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The email is required')
        user = self.model(email=self.normalize_email(email), **extra_fields)
        # Los usuarios de la app entran con Firebase: sin contraseña local
        user.set_password(password) if password else user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        # Solo para el admin de Django; es el único caso con contraseña local
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    # UID de Firebase; vacío solo para cuentas del admin de Django
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    name = models.CharField(max_length=120, blank=True)
    locale = models.CharField(max_length=10, default='es-ES')
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'

    def __str__(self):
        return self.email
