from rest_framework.routers import SimpleRouter

from households.views import UUID_REGEX

from .views import TaskViewSet

router = SimpleRouter()
# Las tareas viven dentro de su hogar: /households/{household_pk}/tasks/
router.register(rf'households/(?P<household_pk>{UUID_REGEX})/tasks', TaskViewSet, basename='task')

urlpatterns = router.urls
