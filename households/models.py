import secrets
import uuid

from django.conf import settings
from django.db import models

# Sin caracteres ambiguos (0/O, 1/I/L) para que el código sea fácil de dictar
JOIN_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
JOIN_CODE_LENGTH = 6
# Largo de la columna: los hogares creados antes tienen códigos de 8
JOIN_CODE_MAX_LENGTH = 8
# La app muestra el código como FLOWY-XXXXXX; el prefijo no se guarda
JOIN_CODE_PREFIX = 'FLOWY-'


def generate_join_code():
    while True:
        code = ''.join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
        if not Household.objects.filter(join_code=code).exists():
            return code


def normalize_join_code(value):
    # Acepta minúsculas, espacios y el código con o sin «FLOWY-»
    code = value.strip().upper().replace(' ', '')
    if code.startswith(JOIN_CODE_PREFIX):
        code = code[len(JOIN_CODE_PREFIX):]
    return code


class Household(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    join_code = models.CharField(max_length=JOIN_CODE_MAX_LENGTH, unique=True, default=generate_join_code)
    # Se mantiene sincronizado con la membresía de rol owner (ver transfer_ownership)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='owned_households',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='Membership',
        related_name='households',
    )

    def regenerate_join_code(self):
        self.join_code = generate_join_code()
        self.save(update_fields=['join_code'])

    def get_membership(self, user):
        return self.memberships.filter(user=user, status=Membership.STATUS_ACTIVE).first()

    def transfer_ownership(self, current, new):
        # `current` y `new` son membresías activas de este hogar
        current.role = Membership.ROLE_ADMIN
        current.save(update_fields=['role'])
        new.role = Membership.ROLE_OWNER
        new.save(update_fields=['role'])
        self.owner_id = new.user_id
        self.save(update_fields=['owner'])

    def __str__(self):
        return self.name


class ActiveMembershipQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=Membership.STATUS_ACTIVE)


class Membership(models.Model):
    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_MEMBER = 'member'

    ROLE_CHOICES = [
        (ROLE_OWNER, 'Owner'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MEMBER, 'Member'),
    ]

    STATUS_INVITED = 'invited'
    STATUS_ACTIVE = 'active'

    STATUS_CHOICES = [
        (STATUS_INVITED, 'Invited'),
        (STATUS_ACTIVE, 'Active'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ActiveMembershipQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['household', 'user'], name='unique_household_membership'),
        ]
        ordering = ['created_at']

    @property
    def is_admin(self):
        return self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)

    @property
    def is_owner(self):
        return self.role == self.ROLE_OWNER

    def __str__(self):
        return f'{self.user} @ {self.household} ({self.role}, {self.status})'
