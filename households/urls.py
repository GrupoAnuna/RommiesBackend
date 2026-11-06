from rest_framework.routers import SimpleRouter

from .views import HouseholdViewSet

app_name = "households"

router = SimpleRouter()
router.register("households", HouseholdViewSet, basename="household")

urlpatterns = router.urls
