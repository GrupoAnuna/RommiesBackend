from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ('email',)
    list_display = ('email', 'name', 'firebase_uid', 'is_staff', 'created_at')
    search_fields = ('email', 'name', 'firebase_uid')
    readonly_fields = ('created_at', 'last_login')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Perfil', {'fields': ('name', 'locale', 'firebase_uid')}),
        ('Permisos', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas', {'fields': ('last_login', 'created_at')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2')}),
    )
