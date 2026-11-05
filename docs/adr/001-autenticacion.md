# ADR 001: Autenticación con Firebase Authentication

- **Estado:** Aceptada
- **Fecha:** 2026-11-04
- **Tareas relacionadas:** SCRUM-10 [DEV-06], SCRUM-6 [DEV-02]

## Contexto

Roomies tiene una app móvil en Flutter y este backend en Django REST Framework. La app necesita
registro, inicio de sesión, verificación de correo y recuperación de contraseña, y el backend necesita
saber qué usuario hace cada petición para aplicar los permisos por hogar (`IsHouseholdMember`, roles
`owner` / `admin` / `member`).

La configuración inicial del backend mezclaba dos mecanismos:

- `FirebaseAuthentication` como única clase de autenticación de DRF, validando el ID token de Firebase.
- Rutas de SimpleJWT (`/api/token/`, `/api/token/refresh/`, `/api/token/verify/`) y la configuración
  `SIMPLE_JWT`, que emitían tokens propios que DRF en realidad no aceptaba.

Además, la app de Flutter ya usa el SDK de Firebase Auth para el registro y la verificación del correo.
Hacía falta elegir una única fuente de verdad para la identidad.

## Decisión

La autenticación se hace **exclusivamente con Firebase Authentication**. El backend no emite tokens
ni guarda contraseñas de los usuarios de la app.

1. La app inicia sesión con el SDK de Firebase y envía el ID token en cada petición:
   `Authorization: Bearer <firebase-id-token>`.
2. `authentication.firebase_auth.FirebaseAuthentication` es la única clase en
   `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`. Verifica el token con
   `firebase_admin.auth.verify_id_token` usando la cuenta de servicio indicada en
   `FIREBASE_CREDENTIALS`.
3. El usuario local (`accounts.User`, id UUID) se identifica por `firebase_uid`:
   - Si no existe, se crea en la primera petición con el `email` y el `name` del token y una
     contraseña inutilizable. No hay endpoint de registro en el backend.
   - Si el UID es nuevo pero el correo ya existe, solo se vuelve a vincular cuando el token trae
     `email_verified: true` (cuenta de Firebase recreada). Si no está verificado, se responde 401.
   - Dos peticiones simultáneas del primer login no crean usuarios duplicados.
4. Un token ausente, inválido o caducado, o un usuario desactivado, recibe **401** con
   `WWW-Authenticate: Bearer`. El 403 queda reservado para usuarios autenticados sin permiso.
5. La única excepción son las cuentas del admin de Django (`/admin/`), que se crean con
   `python manage.py createsuperuser` y tienen contraseña local. No sirven para la API.
6. Se eliminaron las rutas `/api/token/*`, `SIMPLE_JWT` y la dependencia
   `djangorestframework-simplejwt` (commit `7e64e11`).

## Alternativas descartadas

### JWT propio con SimpleJWT

El backend gestionaría las cuentas y emitiría sus propios tokens de acceso y de refresco con
`djangorestframework-simplejwt`. Se descartó porque:

- **Duplica lo que ya hace Firebase en la app.** Habría que construir y mantener en Django el registro,
  el hash de contraseñas (Argon2id), la verificación de correo, la recuperación de contraseña, el
  bloqueo por intentos fallidos y la lista negra de refresh tokens. Firebase ya lo ofrece y la app ya
  lo usa.
- **Dos fuentes de verdad.** Con Firebase en la app y SimpleJWT en el backend habría que sincronizar
  usuarios y contraseñas entre ambos sistemas, o intercambiar el token de Firebase por uno propio.
  Ese paso añade un endpoint, un segundo ciclo de caducidad y más casos de error en la app sin aportar
  seguridad.
- **Más superficie de ataque.** Guardar contraseñas y una clave de firma propia convierte al backend
  en un objetivo más valioso. Con Firebase, el backend solo verifica firmas con las claves públicas
  de Google.
- **Funciones futuras más caras.** Iniciar sesión con Google o Apple, o añadir MFA, se activa en
  Firebase sin tocar el backend. Con JWT propio habría que implementar cada proveedor.

## Consecuencias

### Positivas

- Hay una sola fuente de verdad para la identidad, compartida por la app y el backend.
- El backend no guarda contraseñas de usuarios de la app (`password_hash` desaparece del modelo de
  datos) y queda más simple: no hay registro, login, refresco ni recuperación de contraseña.
- Los proveedores sociales y el MFA se pueden añadir desde la consola de Firebase.
- La app puede llamar a cualquier endpoint justo después de registrarse, porque el usuario se crea
  solo en la primera petición.

### Negativas y riesgos

- **Dependencia de Firebase y de Google Cloud.** Cambiar de proveedor implicaría migrar las cuentas
  y reescribir `FirebaseAuthentication`. El modelo local con `firebase_uid` separado del `id` UUID
  limita ese impacto.
- **La revocación no es inmediata.** `verify_id_token` se llama sin `check_revoked=True`, así que un
  token revocado (cuenta deshabilitada o sesión cerrada en otro dispositivo) sigue siendo válido hasta
  que caduca, como máximo 1 hora. Si hiciera falta revocación inmediata habría que activar
  `check_revoked`, que añade una llamada a Firebase en cada petición.
- **El correo verificado no se exige en la API.** Hoy solo lo comprueba la app. Si se quiere
  reforzar en el servidor, `FirebaseAuthentication` debe rechazar los tokens con
  `email_verified: false`.
- **La cuenta de servicio es un secreto crítico.** `service-account.json` está fuera de git y su ruta
  se configura con `FIREBASE_CREDENTIALS`. Hay que gestionarla en cada entorno y en la CI, y rotarla
  si se filtra.
- **Las cuentas pueden desincronizarse.** Borrar o deshabilitar un usuario en Firebase no borra ni
  desactiva al `accounts.User` local; solo deja de poder autenticarse. Si hiciera falta, se resolvería
  con un proceso aparte (por ejemplo, una Cloud Function que avise al backend).
- **Los tests no llaman a Firebase.** Usan `force_authenticate` o simulan `verify_id_token` con
  `unittest.mock.patch`. No hay test de integración contra Firebase real.
- **El arranque necesita las credenciales.** `config/settings.py` inicializa `firebase_admin` al
  arrancar, así que el servidor y los tests necesitan un `service-account.json` válido. En la CI habrá
  que proporcionarlo como secreto o hacer opcional esa inicialización.
