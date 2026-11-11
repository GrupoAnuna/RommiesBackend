from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .models import Household, Membership
from .permissions import IsHouseholdAdmin, IsHouseholdMember, IsHouseholdOwner
from .serializers import (
    HouseholdPreviewSerializer,
    HouseholdSerializer,
    JoinHouseholdSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
)

# Acciones que reciben un código de invitación: se limitan para que no se
# puedan adivinar códigos a fuerza de intentos
JOIN_CODE_ACTIONS = {'lookup', 'join'}

UUID_REGEX = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


@extend_schema(tags=['Households'])
@extend_schema_view(
    list=extend_schema(summary='Listar mis hogares'),
    create=extend_schema(summary='Crear hogar', description='El usuario que lo crea queda como owner.'),
    retrieve=extend_schema(summary='Ver hogar', description='Solo miembros.'),
    update=extend_schema(summary='Reemplazar hogar', description='Solo admin u owner.'),
    partial_update=extend_schema(summary='Editar hogar', description='Solo admin u owner.'),
    destroy=extend_schema(summary='Borrar hogar', description='Solo el owner.'),
)
class HouseholdViewSet(viewsets.ModelViewSet):
    serializer_class = HouseholdSerializer
    # Un id que no es UUID da 404 en vez de error del servidor
    lookup_value_regex = UUID_REGEX

    # Permisos por acción (además de IsAuthenticated)
    action_permissions = {
        'retrieve': [IsHouseholdMember],
        'members': [IsHouseholdMember],
        'leave': [IsHouseholdMember],
        'update': [IsHouseholdAdmin],
        'partial_update': [IsHouseholdAdmin],
        'regenerate_code': [IsHouseholdAdmin],
        'member_detail': [IsHouseholdAdmin],
        'destroy': [IsHouseholdOwner],
    }

    def get_permissions(self):
        extra = self.action_permissions.get(self.action, [])
        return [IsAuthenticated()] + [permission() for permission in extra]

    def get_throttles(self):
        if self.action in JOIN_CODE_ACTIONS:
            self.throttle_scope = 'join_code'
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        # drf-spectacular genera el esquema sin usuario real
        if getattr(self, 'swagger_fake_view', False):
            return Household.objects.none()

        # Solo los hogares del usuario: para los demás el hogar "no existe" (404)
        my_households = Membership.objects.active().filter(user=self.request.user).values('household')
        return (
            Household.objects.filter(pk__in=my_households)
            .annotate(member_count=Count('memberships', filter=Q(memberships__status=Membership.STATUS_ACTIVE)))
            .order_by('-created_at')
        )

    @transaction.atomic
    def perform_create(self, serializer):
        household = serializer.save(owner=self.request.user)
        Membership.objects.create(household=household, user=self.request.user, role=Membership.ROLE_OWNER)

    @extend_schema(
        summary='Buscar hogar por código',
        description='Muestra el hogar antes de unirse. No cambia nada; is_member indica si ya perteneces.',
        request=JoinHouseholdSerializer,
        responses=HouseholdPreviewSerializer,
    )
    @action(detail=False, methods=['post'])
    def lookup(self, request):
        serializer = JoinHouseholdSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        household = serializer.context['household']
        return Response(HouseholdPreviewSerializer(household, context={'request': request}).data)

    @extend_schema(
        summary='Unirse con código de invitación',
        request=JoinHouseholdSerializer,
        responses=HouseholdSerializer,
    )
    @action(detail=False, methods=['post'])
    def join(self, request):
        serializer = JoinHouseholdSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        household = serializer.context['household']

        membership, created = Membership.objects.get_or_create(household=household, user=request.user)
        if not created:
            if membership.status == Membership.STATUS_ACTIVE:
                raise ValidationError({'join_code': 'You are already a member of this household.'})
            # Tenía una invitación pendiente: unirse con el código la acepta
            membership.status = Membership.STATUS_ACTIVE
            membership.save(update_fields=['status'])

        household = self.get_queryset().get(pk=household.pk)
        data = HouseholdSerializer(household, context={'request': request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary='Salir del hogar',
        description='El owner debe transferir la propiedad antes si quedan otros miembros.',
        request=None,
        responses={204: None},
    )
    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        household = self.get_object()
        membership = household.get_membership(request.user)

        if membership.is_owner:
            if household.memberships.active().exclude(pk=membership.pk).exists():
                raise ValidationError(
                    {'detail': 'Transfer ownership to another member before leaving the household.'}
                )
            # Es el único miembro: salir equivale a borrar el hogar
            household.delete()
        else:
            membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary='Regenerar código de invitación',
        description='Solo admin u owner. El código anterior deja de funcionar.',
        request=None,
        responses=HouseholdSerializer,
    )
    @action(detail=True, methods=['post'], url_path='regenerate-code')
    def regenerate_code(self, request, pk=None):
        household = self.get_object()
        household.regenerate_join_code()
        return Response(self.get_serializer(household).data)

    @extend_schema(summary='Listar miembros', responses=MembershipSerializer(many=True))
    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        household = self.get_object()
        memberships = household.memberships.select_related('user')
        return Response(MembershipSerializer(memberships, many=True).data)

    @extend_schema(
        methods=['PATCH'],
        summary='Cambiar rol de un miembro',
        description='Solo el owner. Asignar role=owner transfiere la propiedad y el owner actual pasa a admin.',
        request=MembershipRoleSerializer,
        responses=MembershipSerializer,
    )
    @extend_schema(
        methods=['DELETE'],
        summary='Expulsar a un miembro',
        description='Admin u owner. Solo el owner puede expulsar a un admin.',
        request=None,
        responses={204: None},
    )
    @action(detail=True, methods=['patch', 'delete'], url_path=rf'members/(?P<user_id>{UUID_REGEX})')
    def member_detail(self, request, pk=None, user_id=None):
        household = self.get_object()
        actor = household.get_membership(request.user)
        target = get_object_or_404(household.memberships.select_related('user'), user_id=user_id)

        if target.pk == actor.pk:
            raise ValidationError({'detail': 'You cannot change or remove your own membership. Use leave instead.'})

        if request.method == 'DELETE':
            return self._remove_member(actor, target)
        return self._change_role(request, actor, target)

    def _remove_member(self, actor, target):
        # Los admins solo pueden expulsar a miembros normales; el owner a cualquiera
        if not actor.is_owner and target.role != Membership.ROLE_MEMBER:
            return Response(
                {'detail': 'Only the household owner can remove admins.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        target.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def _change_role(self, request, actor, target):
        if not actor.is_owner:
            return Response(
                {'detail': 'Only the household owner can change roles.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MembershipRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data['role']

        if role == Membership.ROLE_OWNER:
            if target.status != Membership.STATUS_ACTIVE:
                raise ValidationError({'role': 'Ownership can only be transferred to an active member.'})
            # Transferir la propiedad: el owner actual pasa a admin
            actor.household.transfer_ownership(actor, target)
        else:
            target.role = role
            target.save(update_fields=['role'])
        return Response(MembershipSerializer(target).data)
