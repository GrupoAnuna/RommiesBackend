from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from .models import Task
from .serializers import TaskSerializer


# GET (list) + POST (create)
@extend_schema(tags=['Tasks'])
@extend_schema_view(
    get=extend_schema(summary='Listar tareas'),
    post=extend_schema(summary='Crear tarea'),
)
class TaskListCreateView(generics.ListCreateAPIView):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer

# GET (detail) + PUT/PATCH (update) + DELETE
@extend_schema(tags=['Tasks'])
@extend_schema_view(
    get=extend_schema(summary='Ver tarea'),
    put=extend_schema(summary='Reemplazar tarea'),
    patch=extend_schema(summary='Editar tarea'),
    delete=extend_schema(summary='Borrar tarea'),
)
class TaskRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
from django.shortcuts import render

# Create your views here.
