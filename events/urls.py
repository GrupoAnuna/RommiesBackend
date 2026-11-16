from django.urls import path
from rest_framework.routers import SimpleRouter

from households.views import UUID_REGEX

from .views import CalendarView, EventViewSet

app_name = "events"

router = SimpleRouter()
# Los eventos viven dentro de su hogar: /households/{household_pk}/events/
router.register(rf'households/(?P<household_pk>{UUID_REGEX})/events', EventViewSet, basename='event')

urlpatterns = router.urls + [
    path('households/<uuid:household_pk>/calendar/', CalendarView.as_view(), name='calendar'),
]
