from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.functional import cached_property
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from households.models import Household
from households.views import UUID_REGEX

from .models import Task
from .serializers import (
    ASSIGNEE_ME,
    ASSIGNEE_UNASSIGNED,
    STATUS_DONE,
    STATUS_PENDING,
    CompleteTaskResponseSerializer,
    TaskFilterSerializer,
    TaskSerializer,
)


class TaskPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


HOUSEHOLD_PARAM = OpenApiParameter('household_pk', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del hogar.')
TASK_PARAM = OpenApiParameter('id', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id de la tarea.')


@extend_schema(tags=['Tasks'], parameters=[HOUSEHOLD_PARAM])
@extend_schema_view(
    list=extend_schema(
        summary='Listar tareas del hogar',
        description='Ordenadas por fecha. Paginadas de 50 en 50 (?page=, ?page_size= hasta 100).',
        parameters=[TaskFilterSerializer],
    ),
    create=extend_schema(summary='Crear tarea', description='Cualquier miembro. Solo se asigna a miembros del hogar.'),
    retrieve=extend_schema(summary='Ver tarea', parameters=[TASK_PARAM]),
    update=extend_schema(summary='Reemplazar tarea', parameters=[TASK_PARAM]),
    partial_update=extend_schema(summary='Editar tarea', parameters=[TASK_PARAM]),
    destroy=extend_schema(summary='Borrar tarea', description='Solo admin u owner.', parameters=[TASK_PARAM]),
)
class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    pagination_class = TaskPagination
    lookup_value_regex = UUID_REGEX

    @cached_property
    def household(self):
        # Para quien no es miembro, el hogar "no existe" (404), igual que en /households/
        household = Household.objects.filter(pk=self.kwargs['household_pk']).first()
        membership = household.get_membership(self.request.user) if household else None
        if membership is None:
            raise Http404
        self.membership = membership
        return household

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not getattr(self, 'swagger_fake_view', False):
            self.household  # 404 antes de cualquier acción si no es miembro

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if self.action == 'destroy' and not self.membership.is_admin:
            raise PermissionDenied('Only household admins can delete tasks.')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, 'swagger_fake_view', False):
            context['household'] = self.household
        return context

    def get_queryset(self):
        # drf-spectacular genera el esquema sin usuario ni hogar reales
        if getattr(self, 'swagger_fake_view', False):
            return Task.objects.none()

        queryset = Task.objects.filter(household=self.household).select_related('assignee', 'next_occurrence')
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
            queryset = queryset.filter(assignee=self.request.user)
        elif assignee == ASSIGNEE_UNASSIGNED:
            queryset = queryset.filter(assignee__isnull=True)
        elif assignee is not None:
            queryset = queryset.filter(assignee_id=assignee)

        if 'due_from' in filters:
            queryset = queryset.filter(due_at__gte=filters['due_from'])
        if 'due_to' in filters:
            queryset = queryset.filter(due_at__lte=filters['due_to'])
        return queryset

    def perform_create(self, serializer):
        serializer.save(household=self.household, created_by=self.request.user)

    @extend_schema(
        summary='Completar tarea',
        description=(
            'Cualquier miembro. Si la tarea es semanal crea la de la semana siguiente '
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
            if task.repeat_rule == Task.REPEAT_WEEKLY and next_task is None:
                next_task = task.create_next_occurrence()

        context = self.get_serializer_context()
        return Response({
            'task': TaskSerializer(task, context=context).data,
            'next_task': TaskSerializer(next_task, context=context).data if next_task else None,
        })

    @extend_schema(
        summary='Reabrir tarea',
        description='Deshace el completado. La tarea de la semana siguiente, si ya se creó, se conserva.',
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
