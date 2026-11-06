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

En la primera petición el backend verifica el token y crea el usuario (`accounts.User`, con id UUID,
`email` y `firebase_uid`). Los usuarios de la app no tienen contraseña local: la gestiona Firebase.
Todas las rutas requieren autenticación salvo `/health` y la documentación.

Para entrar al admin de Django (`/admin/`) crea una cuenta con contraseña local:

```bash
python manage.py createsuperuser
```

## Endpoints

| Ruta | Descripción |
|---|---|
| `GET /health` | Estado del servicio (público) |
| `/api/docs/` | Swagger UI |
| `/api/schema/` | Esquema OpenAPI |
| `GET/PATCH /api/v1/me/` | Mi usuario y mis hogares (`households: []` = sin hogar) |
| `GET/POST /api/v1/households/` | Mis hogares / crear hogar (el creador es `owner`) |
| `GET/PATCH/DELETE /api/v1/households/<id>/` | Ver (miembro) / editar (admin) / borrar (owner) |
| `POST /api/v1/households/join/` | Unirse con `{"join_code": "..."}` |
| `POST /api/v1/households/<id>/leave/` | Salir del hogar |
| `POST /api/v1/households/<id>/regenerate-code/` | Nuevo código de invitación (admin) |
| `GET /api/v1/households/<id>/members/` | Listar miembros |
| `PATCH/DELETE /api/v1/households/<id>/members/<user_id>/` | Cambiar rol (owner) / expulsar (admin) |
| `/api/v1/tasks/` | Listar / crear tareas |
| `/api/v1/tasks/<id>/` | Ver / editar / borrar una tarea |

## Hogares y roles

Cada usuario puede pertenecer a varios hogares con uno de estos roles:

| Acción | member | admin | owner |
|---|:-:|:-:|:-:|
| Ver hogar y miembros, salir | ✅ | ✅ | ✅ |
| Editar nombre, regenerar código, expulsar a `member` | | ✅ | ✅ |
| Cambiar roles, expulsar a `admin`, borrar hogar | | | ✅ |

- Para transferir la propiedad, el owner asigna `role: "owner"` a otro miembro y pasa a ser `admin`.
- El owner no puede salir mientras haya otros miembros; si es el único, salir borra el hogar.
- Los no miembros reciben 404, así no se revela que el hogar existe.
- Una membresía `invited` aún no da acceso; al unirse con el código pasa a `active`.
- `households.owner` se mantiene sincronizado con la membresía de rol `owner`.

Para otras apps, `households.permissions` ofrece `IsHouseholdMember`, `IsHouseholdAdmin` e
`IsHouseholdOwner`, que funcionan con un `Household` o con cualquier objeto que tenga FK `household`.

## Tests

```bash
python manage.py test
```

## Estructura

```
config/          Configuración del proyecto (settings, urls)
accounts/        Usuario (UUID + Firebase) y endpoint /me/
authentication/  Autenticación con Firebase para DRF
households/      Hogares, miembros, roles y permisos
tasks/           Tareas del hogar
analytics/ events/ expenses/ files/ profiles/   (pendientes)
```
