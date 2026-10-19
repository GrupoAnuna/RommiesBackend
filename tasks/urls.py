from django.urls import path
from .views import TasksListCreateView, TasksRetrieveUpdateDeleteView

urlpatterns = [
    path('tasks/', TasksListCreateView.as_view(), name='tasks-list'),
    path('tasks/<int:pk>/', TasksRetrieveUpdateDeleteView.as_view(), name='tasks-detail'),
]
