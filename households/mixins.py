from django.http import Http404
from django.utils.functional import cached_property
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework.pagination import PageNumberPagination

from .models import Household

HOUSEHOLD_PARAM = OpenApiParameter('household_pk', OpenApiTypes.UUID, OpenApiParameter.PATH, description='Id del hogar.')


class HouseholdPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


class HouseholdScopedMixin:
    """Para viewsets anidados en /households/{household_pk}/...

    Expone `self.household` y `self.membership`, y pasa el hogar al serializer.
    Para quien no es miembro, el hogar "no existe" (404), igual que en /households/.
    """

    @property
    def is_schema_view(self):
        # drf-spectacular genera el esquema sin usuario ni hogar reales
        return getattr(self, 'swagger_fake_view', False)

    @cached_property
    def household(self):
        household = Household.objects.filter(pk=self.kwargs['household_pk']).first()
        membership = household.get_membership(self.request.user) if household else None
        if membership is None:
            raise Http404
        self.membership = membership
        return household

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not self.is_schema_view:
            self.household  # 404 antes de cualquier acción si no es miembro

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not self.is_schema_view:
            context['household'] = self.household
        return context
