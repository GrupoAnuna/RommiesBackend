from django.db import transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from households.mixins import HOUSEHOLD_PARAM, HouseholdPagination, HouseholdScopedMixin
from households.views import UUID_REGEX

from .models import Task
from .serializers import (
    ASSIGNEE_ME,
    STATUS_DONE,
    STATUS_PENDING,
    CompleteTaskResponseSerializer,
    TaskFilterSerializer,
    TaskSerializer,
)


TASK_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id de la tarea.')


@extend_schema(tags=['Tasks'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar tareas del hogar',
        description='Ordenadas por fecha. Paginadas de 50 en 50 (?page=, ?page_size= hasta 100).',
        parameters=[TaskFilterSerializer],
    ),
    create=extend_schema(
        summary='Crear tarea',
        description='Cualquier miembro. Al menos un responsable, todos miembros del hogar.',
    ),
    retrieve=extend_schema(summary='Ver tarea', parameters=[TASK_PARAM]),
    update=extend_schema(summary='Reemplazar tarea', parameters=[TASK_PARAM]),
    partial_update=extend_schema(summary='Editar tarea', parameters=[TASK_PARAM]),
    destroy=extend_schema(
        summary='Borrar tarea',
        description='Quien la creó, o un admin u owner.',
        parameters=[TASK_PARAM],
    ),
)
class TaskViewSet(HouseholdScopedMixin, viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    pagination_class = HouseholdPagination
    lookup_value_regex = UUID_REGEX

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if self.action == 'destroy' and not (obj.created_by_id == request.user.pk or self.membership.is_admin):
            raise PermissionDenied('Only the creator or a household admin can delete this task.')

    def get_queryset(self):
        if self.is_schema_view:
            return Task.objects.none()

        queryset = (
            Task.objects.filter(household=self.household)
            .select_related('next_occurrence')
            .prefetch_related('assignees')
        )
        if self.action == 'list':
            queryset = self._filter(queryset)
        return queryset

    def _filter(self, queryset):
        params = TaskFilterSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        filters = params.validated_data

        status_filter = filters.get('status')
        if status_filter == STATUS_PENDING:
            queryset = queryset.filter(completed_at__isnull=True)
        elif status_filter == STATUS_DONE:
            queryset = queryset.filter(completed_at__isnull=False)

        assignee = filters.get('assignee')
        if assignee == ASSIGNEE_ME:
            queryset = queryset.filter(assignees=self.request.user)
        elif assignee is not None:
            queryset = queryset.filter(assignees=assignee)

        if 'due_from' in filters:
            queryset = queryset.filter(due_date__gte=filters['due_from'])
        if 'due_to' in filters:
            queryset = queryset.filter(due_date__lte=filters['due_to'])
        return queryset

    def perform_create(self, serializer):
        serializer.save(household=self.household, created_by=self.request.user)

    @extend_schema(
        summary='Completar tarea',
        description=(
            'Cualquier miembro. Si la tarea se repite crea la del siguiente día marcado '
            '(una sola vez, aunque se reabra y se vuelva a completar).'
        ),
        request=None,
        parameters=[TASK_PARAM],
        responses=CompleteTaskResponseSerializer,
    )
    @action(detail=True, methods=['post'])
    def complete(self, request, household_pk=None, pk=None):
        with transaction.atomic():
            task = self.get_object()
            if not task.is_done:
                task.completed_at = timezone.now()
                task.completed_by = request.user
                task.save(update_fields=['completed_at', 'completed_by', 'updated_at'])

            next_task = task.get_next_occurrence()
            if task.repeat_days and next_task is None:
                next_task = task.create_next_occurrence()

        context = self.get_serializer_context()
        return Response({
            'task': TaskSerializer(task, context=context).data,
            'next_task': TaskSerializer(next_task, context=context).data if next_task else None,
        })

    @extend_schema(
        summary='Reabrir tarea',
        description='Deshace el completado. La siguiente repetición, si ya se creó, se conserva.',
        request=None,
        parameters=[TASK_PARAM],
        responses=TaskSerializer,
    )
    @action(detail=True, methods=['post'])
    def reopen(self, request, household_pk=None, pk=None):
        task = self.get_object()
        if task.is_done:
            task.completed_at = None
            task.completed_by = None
            task.save(update_fields=['completed_at', 'completed_by', 'updated_at'])
        return Response(self.get_serializer(task).data)
