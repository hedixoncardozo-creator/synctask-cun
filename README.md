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