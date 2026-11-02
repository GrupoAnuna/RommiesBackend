"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.response import Response
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, inline_serializer
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import serializers

@extend_schema(responses=inline_serializer(
    name="Health",
    fields={"status": serializers.CharField(), "version": serializers.CharField()},
))
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(_request):
    return Response({"status": "ok", "version": "v1"})

urlpatterns = [
    path("admin/", admin.site.urls),

    # Salud del servicio (sin auth)
    path("health", health),

    # Documentación OpenAPI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # API v1 (agregaremos routers por app)
    path("api/v1/", include([
        path("", include("accounts.urls")),
        path("", include("analytics.urls")), 
        path("", include("events.urls")),
        path("", include("expenses.urls")),
        path("", include("files.urls")),
        path("", include("households.urls")),
        path("", include("profiles.urls")),
        path("", include("tasks.urls")),
    ])),
]
