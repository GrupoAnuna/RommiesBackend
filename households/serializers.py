from rest_framework import serializers

from .models import Household, Membership, normalize_join_code


class HouseholdSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = ['id', 'name', 'join_code', 'owner', 'created_at', 'my_role', 'member_count']
        read_only_fields = ['join_code', 'owner', 'created_at']

    def get_member_count(self, obj) -> int:
        # Viene anotado desde el queryset del viewset; si no, se cuenta
        count = getattr(obj, 'member_count', None)
        return count if count is not None else obj.memberships.active().count()

    def get_my_role(self, obj) -> str | None:
        membership = obj.get_membership(self.context['request'].user)
        return membership.role if membership else None


class JoinHouseholdSerializer(serializers.Serializer):
    # Admite el formato que muestra la app: «FLOWY-XXXXXX»
    join_code = serializers.CharField(max_length=20)

    def validate_join_code(self, value):
        code = normalize_join_code(value)
        try:
            self.context['household'] = Household.objects.get(join_code=code)
        except Household.DoesNotExist:
            raise serializers.ValidationError('Invalid join code.', code='invalid_join_code')
        return code


class HouseholdPreviewSerializer(serializers.ModelSerializer):
    """Lo que ve quien tiene el código antes de unirse (pantalla «¿Es este tu hogar?»)."""

    member_count = serializers.SerializerMethodField()
    member_names = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source='owner.name', read_only=True)
    is_member = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = ['id', 'name', 'member_count', 'member_names', 'owner_name', 'is_member']

    def _active_memberships(self, obj):
        return obj.memberships.active().select_related('user')

    def get_member_count(self, obj) -> int:
        return self._active_memberships(obj).count()

    def get_member_names(self, obj) -> list[str]:
        # Solo los nombres, para las iniciales de los avatares; nunca los correos
        return [membership.user.name for membership in self._active_memberships(obj)]

    def get_is_member(self, obj) -> bool:
        return obj.get_membership(self.context['request'].user) is not None


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    name = serializers.CharField(source='user.name', read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'user_id', 'email', 'name', 'role', 'status', 'created_at']
        read_only_fields = ['status', 'created_at']


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.ROLE_CHOICES)
