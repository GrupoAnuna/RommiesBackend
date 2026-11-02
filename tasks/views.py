from rest_framework import generics
from .models import Task
from .serializers import TaskSerializer


# GET (list) + POST (create)
class TaskListCreateView(generics.ListCreateAPIView):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer

# GET (detail) + PUT/PATCH (update) + DELETE
class TaskRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
