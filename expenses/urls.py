from rest_framework.routers import SimpleRouter

from households.views import UUID_REGEX

from .views import ExpenseViewSet

app_name = "expenses"

router = SimpleRouter()
# Los gastos viven dentro de su hogar: /households/{household_pk}/expenses/
router.register(rf'households/(?P<household_pk>{UUID_REGEX})/expenses', ExpenseViewSet, basename='expense')

urlpatterns = router.urls
