import secrets

from django.conf import settings
from django.db import models

# Sin caracteres ambiguos (0/O, 1/I/L) para que el código sea fácil de dictar
INVITE_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
INVITE_CODE_LENGTH = 8


def generate_invite_code():
    while True:
        code = ''.join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))
        if not Household.objects.filter(invite_code=code).exists():
            return code


class Household(models.Model):
    name = models.CharField(max_length=120)
    invite_code = models.CharField(max_length=INVITE_CODE_LENGTH, unique=True, default=generate_invite_code)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_households',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='Membership',
        related_name='households',
    )

    def regenerate_invite_code(self):
        self.invite_code = generate_invite_code()
        self.save(update_fields=['invite_code'])

    def get_membership(self, user):
        return self.memberships.filter(user=user).first()

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_MEMBER = 'member'

    ROLE_CHOICES = [
        (ROLE_OWNER, 'Owner'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MEMBER, 'Member'),
    ]

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['household', 'user'], name='unique_household_membership'),
        ]
        ordering = ['joined_at']

    @property
    def is_admin(self):
        return self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)

    @property
    def is_owner(self):
        return self.role == self.ROLE_OWNER

    def __str__(self):
        return f'{self.user} @ {self.household} ({self.role})'
