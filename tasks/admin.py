from django.contrib import admin

from .models import Tasks

# Mostrar la tabla desde el admin de DJANGO 
admin.site.register(Tasks)