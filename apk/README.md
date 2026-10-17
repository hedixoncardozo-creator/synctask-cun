# APK de SyncTask CUN

Esta carpeta compila la aplicacion que se instala en el telefono. No contiene
la logica de SyncTask: esa vive en el `main.py` de la raiz del repositorio y
corre en el servidor.

## Que hay aqui

| Archivo | Para que sirve |
|---|---|
| `main.py` | La cascara. Se conecta al servidor y pinta sus pantallas. |
| `pyproject.toml` | Metadatos, dependencias, permisos y colores del APK. |
| `assets/` | Icono y pantalla de presentacion de la marca. |

## Por que la aplicacion no va completa dentro del telefono

Dos razones, y la segunda es la que decide la arquitectura.

**Credenciales.** SyncTask lee y escribe en DynamoDB. Si el codigo de acceso
viajara dentro del APK, cualquiera podria extraerlo del archivo y entrar a la
base de datos. En el servidor no hay credenciales guardadas: las toma del rol
de instancia de IAM, que no se puede copiar.

**Sincronizacion.** El PubSub de Flet funciona dentro de un proceso. Si cada
telefono ejecutara su propia copia completa, cada telefono seria un proceso
aislado y una tarea creada aqui no llegaria nunca al telefono del compañero.
Eso es exactamente el REQ-05, el requisito central del proyecto.

Con un solo proceso en el servidor y todos los telefonos conectados a el, hay
un unico sitio donde ocurren los cambios y desde donde se avisan. Es el mismo
reparto de cualquier aplicacion colaborativa.

Para quien la usa no hay diferencia: se instala, aparece con su icono y se
abre como cualquier otra.

## Lo que si corre en el telefono

El dibujado de la interfaz, los gestos, la vibracion y las notificaciones del
sistema. La extension `flet-notifications` se compila dentro del APK; el
servidor solo pide que se muestre el aviso y el telefono lo publica en la
bandeja.

**Las dos mitades tienen que coincidir.** `flet-notifications` debe estar
declarada en `pyproject.toml` (lado Dart, dentro del APK) **y** instalada en
el servidor (lado Python). Si falta en uno de los dos, las notificaciones del
sistema no salen. En el servidor:

```
~/venv/bin/pip install flet-notifications==0.1.4
sudo systemctl restart synctask
```

El `main.py` del servidor solo engancha el servicio cuando quien se conecta es
un telefono, nunca un navegador: el cliente web es un paquete ya construido
que no lleva la mitad Dart y no se le puede pedir un servicio que no sabe
crear.

## Compilar

Desde esta carpeta:

```
flet build apk
```

El APK queda en `build/apk/app-release.apk`.

La primera compilacion descarga el SDK de Flutter y el de Android: son varios
gigabytes y puede tardar bastante. **Compruebe el espacio libre antes de
empezar** — con `df -h /` en el servidor, o en las propiedades del disco en
Windows. La ultima vez el servidor estaba al 85%, y ahi no cabe.

Para numerar las versiones:

```
flet build apk --build-version 1.0.0 --build-number 1
```

El numero de compilacion debe subir en cada APK nuevo que se instale encima
del anterior; si no, Android se niega a actualizar.

## Si cambia la IP del servidor

La direccion esta en la constante `SERVIDOR`, al principio de `main.py`. Es el
unico sitio donde aparece, pero cambiarla obliga a recompilar y reinstalar el
APK en todos los telefonos. Por eso se usa el dominio `nip.io` con certificado
de Let's Encrypt y no la IP desnuda: el nombre se mantiene mientras la IP
elastica no cambie, y la conexion va cifrada.

## Instalar en el telefono

1. Pasar el `.apk` al telefono (cable, correo o descarga).
2. Abrirlo. Android pedira permiso para instalar aplicaciones de esta
   procedencia; hay que concederlo una vez.
3. Al abrir la aplicacion por primera vez pedira permiso de notificaciones.

## Lo que esta comprobado y lo que no

Comprobado con un servidor real y un navegador de verdad:

- La cascara renderiza la aplicacion remota completa.
- Con el servidor apagado aparece la pantalla de error, no un fallo crudo.
- Al encender el servidor y pulsar "Volver a intentar", la aplicacion carga.
- El servicio de notificaciones no se engancha a clientes web.

**No comprobado todavia**, porque hace falta un telefono y el APK construido:
que la notificacion del sistema salga efectivamente en la bandeja de Android a
traves de la cascara. El codigo esta escrito para que, si eso falla, no se
rompa nada: el aviso dentro de la pantalla sigue saliendo igual. Conviene
declararlo asi en el informe hasta tener la prueba en el telefono.

## Limitaciones que conviene declarar en el informe

- La aplicacion necesita conexion y necesita la instancia de AWS encendida. Lo
  primero ya era cierto por ser un tablero compartido; lo segundo hay que
  tenerlo presente el dia de la sustentacion.
- La notificacion es *local*: la lanza la propia aplicacion al recibir el aviso
  del servidor. Con la aplicacion abierta o minimizada llega; con la aplicacion
  cerrada del todo, no. Cubrir ese caso exige un servicio de push —Firebase u
  otro—, fuera del alcance de esta entrega.
