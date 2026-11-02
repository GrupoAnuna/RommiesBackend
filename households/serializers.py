from rest_framework import serializers

from .models import Household, Membership


class HouseholdSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = ['id', 'name', 'invite_code', 'created_by', 'created_at', 'my_role', 'member_count']
        read_only_fields = ['invite_code', 'created_by', 'created_at']

    def get_member_count(self, obj) -> int:
        # Viene anotado desde el queryset del viewset; si no, se cuenta
        count = getattr(obj, 'member_count', None)
        return count if count is not None else obj.memberships.count()

    def get_my_role(self, obj) -> str | None:
        membership = obj.get_membership(self.context['request'].user)
        return membership.role if membership else None


class JoinHouseholdSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=8)

    def validate_invite_code(self, value):
        code = value.strip().upper()
        try:
            self.context['household'] = Household.objects.get(invite_code=code)
        except Household.DoesNotExist:
            raise serializers.ValidationError('Invalid invite code.')
        return code


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'user_id', 'email', 'role', 'joined_at']
        read_only_fields = ['joined_at']


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.ROLE_CHOICES)
