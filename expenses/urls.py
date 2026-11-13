from django.urls import path
from rest_framework.routers import SimpleRouter

from households.views import UUID_REGEX

from .views import ActivityView, BalancesView, ExpenseViewSet, PaymentViewSet, ReceiptViewSet

app_name = "expenses"

HOUSEHOLD = rf'households/(?P<household_pk>{UUID_REGEX})'

router = SimpleRouter()
# Los gastos viven dentro de su hogar: /households/{household_pk}/expenses/
router.register(rf'{HOUSEHOLD}/expenses', ExpenseViewSet, basename='expense')
router.register(rf'{HOUSEHOLD}/payments', PaymentViewSet, basename='payment')
router.register(rf'{HOUSEHOLD}/receipts', ReceiptViewSet, basename='receipt')

urlpatterns = router.urls + [
    path('households/<uuid:household_pk>/balances/', BalancesView.as_view(), name='balances'),
    path('households/<uuid:household_pk>/activity/', ActivityView.as_view(), name='activity'),
]
