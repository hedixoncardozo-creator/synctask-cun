@"
# SyncTask CUN

Aplicación móvil colaborativa para la gestión de tareas académicas con
sincronización en tiempo real mediante Flet y servicios de AWS.

**Asignatura:** Programación para Dispositivos Móviles · Grupo 30103 · 2026C
**Docente:** Daniel Alejandro García Rodríguez
**Integrantes:** Luis Manchego · Hedixon Felipe Cardozo Bejarano

## Arquitectura

- **Cliente:** aplicación Flet (Android y web)
- **Servidor:** Amazon EC2 con Nginx como proxy inverso y TLS
- **Datos:** Amazon DynamoDB, diseño de tabla única
- **Tiempo real:** mecanismo de publicación y suscripción de Flet

## Estructura

- ``modulos/`` — sesión, proyectos, tareas y comentarios
- ``datos/`` — capa de acceso a DynamoDB
- ``tests/`` — pruebas unitarias con pytest
- ``docs/`` — bitácoras y evidencias

## Entorno

Desplegado sobre AWS Academy Learner Lab, región us-east-1.
Las credenciales se obtienen del perfil de instancia IAM; no se
almacenan claves en el repositorio.
"@ | Out-File -FilePath README.md -Encoding utf8

## Acceso con cuenta de Google

Reemplaza el paso de autenticación, no la sesión: una vez Google confirma la
identidad se emite el mismo testigo de siempre, así que el arranque recordado
y las notificaciones siguen funcionando igual.

**La cuenta se enlaza por correo verificado.** Si el correo que entrega Google
ya tiene cuenta en SyncTask, se entra a esa cuenta en lugar de crear una
segunda. Es seguro porque solo se acepta cuando Google marca el correo como
verificado, que es la garantía de que el titular controla ese buzón.

### Configuración en la consola de Google

1. Crear un cliente OAuth de tipo **Aplicación web**.
2. Registrar como URI de redirección autorizada:
   `https://52-54-228-167.nip.io/oauth_callback`
3. En la pantalla de consentimiento, añadir como usuarios de prueba los
   correos que vayan a usarla, mientras el proyecto esté en modo *Testing*.

### Configuración en el servidor

Las credenciales **no** van al repositorio. Se crean a mano en el servidor:

```bash
sudo mkdir -p /etc/synctask
sudo nano /etc/synctask/google.env
```

Con estas tres líneas:

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URL=https://52-54-228-167.nip.io/oauth_callback
```

Y después:

```bash
sudo chmod 600 /etc/synctask/google.env
sudo cp ~/synctask/despliegue/synctask.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart synctask
```

Si falta el archivo, la aplicación arranca igual y simplemente no ofrece el
botón de Google. Es deliberado: un botón que no puede funcionar es peor que
ningún botón.

### Comprobación

```bash
cd ~/synctask && python3 prueba_google.py
```

No toca la red ni DynamoDB. Cubre la validación del perfil (incluida la
trampa de la cadena `"false"`, que en Python es verdadera), el enlace con una
cuenta que ya tenía contraseña y el formulario clásico contra una cuenta de
Google.
