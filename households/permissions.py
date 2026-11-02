from rest_framework import permissions

from .models import Household, Membership


def get_household(obj):
    # Acepta un Household o cualquier objeto con FK `household` (tareas, gastos, ...)
    if isinstance(obj, Household):
        return obj
    return getattr(obj, 'household', None)


def get_membership(user, obj):
    household = get_household(obj)
    if household is None or not user or not user.is_authenticated:
        return None
    return Membership.objects.filter(household=household, user=user).first()


class IsHouseholdMember(permissions.BasePermission):
    """El usuario pertenece al hogar del objeto."""
    message = 'You are not a member of this household.'

    def has_object_permission(self, request, view, obj):
        return get_membership(request.user, obj) is not None


class IsHouseholdAdmin(permissions.BasePermission):
    """El usuario es admin u owner del hogar del objeto."""
    message = 'Only household admins can perform this action.'

    def has_object_permission(self, request, view, obj):
        membership = get_membership(request.user, obj)
        return membership is not None and membership.is_admin


class IsHouseholdOwner(permissions.BasePermission):
    """El usuario es el owner del hogar del objeto."""
    message = 'Only the household owner can perform this action.'

    def has_object_permission(self, request, view, obj):
        membership = get_membership(request.user, obj)
        return membership is not None and membership.is_owner
