# Roomies Backend

API REST de la app Roomies (Flat-Flow API), construida con Django 5.2 y Django REST Framework.
La autenticación se hace exclusivamente con **Firebase Authentication**.

## Requisitos

- Python 3.13
- Una cuenta de servicio de Firebase (archivo JSON)

## Instalación

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
```

1. Copia `.env.example` como `.env` y rellena los valores. Para generar `DJANGO_SECRET_KEY`:

   ```bash
   python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
   ```

2. Descarga la cuenta de servicio desde la consola de Firebase
   (*Configuración del proyecto → Cuentas de servicio → Generar nueva clave privada*)
   y guárdala como `service-account.json` en la raíz del proyecto, o indica otra ruta en `FIREBASE_CREDENTIALS`.

3. Aplica las migraciones y arranca el servidor:

   ```bash
   python manage.py migrate
   python manage.py runserver
   ```

## Variables de entorno

| Variable | Descripción | Por defecto |
|---|---|---|
| `DJANGO_SECRET_KEY` | Clave secreta de Django (obligatoria) | — |
| `DJANGO_DEBUG` | Modo depuración | `False` |
| `DJANGO_ALLOWED_HOSTS` | Hosts permitidos, separados por comas | `localhost,127.0.0.1` |
| `CORS_ALLOW_ALL_ORIGINS` | Permitir cualquier origen (solo dev) | igual que `DJANGO_DEBUG` |
| `CORS_ALLOWED_ORIGINS` | Orígenes permitidos en prod, separados por comas | vacío |
| `FIREBASE_CREDENTIALS` | Ruta al JSON de la cuenta de servicio | `service-account.json` |

## Autenticación

El cliente inicia sesión con Firebase y envía el **ID token** en cada petición:

```
Authorization: Bearer <firebase-id-token>
```

El backend verifica el token y crea el usuario de Django la primera vez (`username` = UID de Firebase).
Todas las rutas requieren autenticación salvo `/health` y la documentación.

## Endpoints

| Ruta | Descripción |
|---|---|
| `GET /health` | Estado del servicio (público) |
| `/api/docs/` | Swagger UI |
| `/api/schema/` | Esquema OpenAPI |
| `/api/v1/tasks/` | Listar / crear tareas |
| `/api/v1/tasks/<id>/` | Ver / editar / borrar una tarea |

## Estructura

```
config/          Configuración del proyecto (settings, urls)
authentication/  Autenticación con Firebase para DRF
tasks/           Tareas del hogar
accounts/ analytics/ events/ expenses/ files/ households/ profiles/   (pendientes)
```
