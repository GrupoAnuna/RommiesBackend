from rest_framework import serializers

from households.models import Membership

from .models import User


class MyHouseholdSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source='household.id', read_only=True)
    name = serializers.CharField(source='household.name', read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'name', 'role']


class MeSerializer(serializers.ModelSerializer):
    # Hogares activos del usuario; lista vacía = usuario sin hogar
    households = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'locale', 'created_at', 'households']
        read_only_fields = ['id', 'email', 'created_at']

    def get_households(self, obj) -> list[dict]:
        memberships = obj.memberships.active().select_related('household').order_by('created_at')
        return MyHouseholdSerializer(memberships, many=True).data
