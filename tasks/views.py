from rest_framework import generics
from .models import Tasks
from .serializers import TasksSerializer

from rest_framework.permissions import IsAuthenticated


# GET (list) + POST (create)
class TasksListCreateView(generics.ListCreateAPIView):
    queryset = Tasks.objects.all()
    serializer_class = TasksSerializer

# GET (detail) + PUT/PATCH (update) + DELETE
class TasksRetrieveUpdateDeleteView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tasks.objects.all()
    serializer_class = TasksSerializer
