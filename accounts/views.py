from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics

from .serializers import MeSerializer


@extend_schema(tags=['Me'])
@extend_schema_view(
    get=extend_schema(
        summary='Mi usuario y mis hogares',
        description='`households` vacío significa que el usuario aún no pertenece a ningún hogar.',
    ),
    put=extend_schema(summary='Reemplazar mi perfil'),
    patch=extend_schema(summary='Editar mi nombre o idioma'),
)
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer

    def get_object(self):
        return self.request.user
