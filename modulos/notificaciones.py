"""Notificaciones del sistema en Android, sin Firebase (REQ-05, REQ-13).

Se apoya en la extension `flet-notifications`, que envuelve el complemento de
Flutter `flutter_local_notifications`. Es una notificacion *local*: la lanza la
propia aplicacion cuando recibe el aviso de PubSub, no un servidor de push.

De ahi se desprende el limite que conviene tener presente y declarar en el
informe: la aplicacion tiene que estar viva para recibir el aviso. El servidor
lanza la notificacion por la conexion abierta; si Android suspendio o mato el
proceso, no hay conexion por la que avise nada.

El servicio en primer plano de mas abajo es la mitigacion posible sin salir de
lo nativo, y conviene ser exacto sobre lo que consigue y lo que no.

Lo que si: mantiene vivo el proceso —y con el la conexion— cuando la persona
sale de la aplicacion o bloquea la pantalla, a cambio de un aviso permanente
en la bandeja. Ese es el caso que se reporto en el uso real y es el que
resuelve.

Lo que no: no es una garantia. Android puede restringir la red en reposo
prolongado, y varios fabricantes —Xiaomi, Huawei, Samsung, Oppo— matan
servicios en primer plano con sus propios gestores de bateria, a veces aunque
el usuario no lo pida. En esos telefonos hay que eximir a la aplicacion del
ahorro de bateria a mano, y aun asi puede fallar.

La unica forma de que una notificacion llegue con la aplicacion cerrada del
todo, de manera fiable, es un servicio de push: el sistema operativo despierta
la aplicacion desde fuera. Eso significa Firebase u otro servicio equivalente,
que se descarto por decision del proyecto. Conviene que esa decision y este
limite queden escritos juntos en el informe, porque el uno es consecuencia del
otro.

Todo el modulo esta escrito para desaparecer sin ruido cuando no se puede
usar. En el navegador no hay notificaciones del sistema, y en el servidor la
extension puede ni siquiera estar instalada; en ambos casos las funciones no
hacen nada y la aplicacion sigue funcionando con los avisos de dentro de la
pantalla. Una notificacion es una comodidad: que falle no puede impedir que se
trabaje.
"""

import asyncio

try:
    from flet_notifications import LocalNotifications

    DISPONIBLE = True
except Exception:  # noqa: BLE001
    LocalNotifications = None
    DISPONIBLE = False

CANAL = "synctask_cambios"

# Canal e identificador del aviso permanente del servicio en primer plano. El
# identificador no puede ser 0 —la extension lo rechaza— y se mantiene fijo
# para que arrancar dos veces no deje dos avisos en la bandeja.
CANAL_VIGILANCIA = "synctask_vigilancia"
AVISO_DE_VIGILANCIA = 1

# Tipo de servicio que se declara ante Android. "dataSync" es el que
# corresponde a mantener datos sincronizados con un servidor, que es
# literalmente lo que hace esta aplicacion. Desde Android 14 el tipo es
# obligatorio y tiene que coincidir con el declarado en el manifiesto.
TIPO_DE_SERVICIO = "dataSync"

_contador = {"n": 1000}


# --- Diagnostico ---------------------------------------------------------
#
# Todas las funciones de este modulo se tragan sus errores a proposito: una
# notificacion es una comodidad y que falle no puede impedir trabajar. El
# precio de esa decision es que, cuando algo no funciona, no queda rastro.
#
# Ya paso dos veces. Se declaro el servicio en primer plano, se comprobo que
# estaba en el manifiesto del APK, y aun asi las notificaciones seguian sin
# llegar con la aplicacion cerrada, sin una sola linea que dijera por que.
#
# De ahi este registro. No cambia el comportamiento —se sigue tragando todo—
# pero deja escrito en el diario del servicio que se intento y como salio. Se
# lee con:
#
#     journalctl -u synctask -n 200 --no-pager | grep NOTIF
#
# Cada linea lleva el prefijo NOTIF para poder filtrarla.

def registrar(mensaje):
    """Escribe una linea en el diario del servicio. Nunca falla."""
    try:
        print(f"NOTIF {mensaje}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def _fallo(donde, error):
    registrar(f"{donde} FALLO {type(error).__name__}: {error}")


def crear_servicio():
    """Devuelve el servicio, o None si la extension no esta disponible."""
    if not DISPONIBLE:
        registrar("crear_servicio: la extension flet_notifications NO se pudo "
                  "importar en el servidor")
        return None
    try:
        servicio = LocalNotifications()
        registrar("crear_servicio: servicio creado")
        return servicio
    except Exception as error:  # noqa: BLE001
        _fallo("crear_servicio", error)
        return None


def preparar(bucle, servicio):
    """Inicializa el servicio, pide permisos y deja constancia del resultado.

    Se programa en el bucle de eventos porque la extension es asincrona. No se
    espera el resultado: si el usuario tarda en responder al permiso, la
    aplicacion no debe quedarse esperandolo.
    """
    if servicio is None or bucle is None:
        registrar(f"preparar: no se puede (servicio={servicio is not None}, "
                  f"bucle={bucle is not None})")
        return

    async def arranque():
        try:
            await servicio.initialize()
            registrar("initialize: OK")
        except Exception as error:  # noqa: BLE001
            _fallo("initialize", error)
            return

        try:
            concedido = await servicio.request_permissions()
            registrar(f"request_permissions: {concedido}")
        except Exception as error:  # noqa: BLE001
            _fallo("request_permissions", error)

        # Esta es la pregunta que de verdad importa: si Android dice que no,
        # ninguna notificacion va a aparecer por mas que se pidan.
        try:
            activadas = await servicio.are_notifications_enabled()
            registrar(f"are_notifications_enabled: {activadas}"
                      + ("" if activadas else
                         "  <-- ANDROID LAS TIENE BLOQUEADAS PARA ESTA APP"))
        except Exception as error:  # noqa: BLE001
            _fallo("are_notifications_enabled", error)

    try:
        asyncio.run_coroutine_threadsafe(arranque(), bucle)
    except Exception as error:  # noqa: BLE001
        _fallo("preparar/programar", error)


def mostrar(bucle, servicio, titulo, cuerpo):
    """Lanza una notificacion del sistema. Nunca lanza excepciones.

    Puede invocarse desde el hilo de PubSub, que es donde llegan los cambios
    de los companeros; por eso se usa run_coroutine_threadsafe y no una
    llamada directa.
    """
    if servicio is None or bucle is None or not cuerpo:
        return

    _contador["n"] += 1
    identificador = _contador["n"]

    async def lanzar():
        try:
            await servicio.show(
                id=identificador,
                title=titulo or "SyncTask CUN",
                body=cuerpo,
                channel_id=CANAL,
                # Se agrupan bajo una misma clave para que varios cambios
                # seguidos no llenen la bandeja con avisos sueltos.
                group_key=CANAL,
            )
            registrar(f"show #{identificador}: OK  {cuerpo[:60]!r}")
        except Exception as error:  # noqa: BLE001
            _fallo(f"show #{identificador}", error)

    try:
        asyncio.run_coroutine_threadsafe(lanzar(), bucle)
    except Exception as error:  # noqa: BLE001
        _fallo("mostrar/programar", error)


def iniciar_vigilancia(bucle, servicio):
    """Arranca el servicio en primer plano. Devuelve si se pudo programar.

    El valor devuelto dice que la peticion salio, no que Android la haya
    aceptado: la llamada es asincrona y el resultado real se vera en el
    telefono. Se devuelve igualmente para que quien llame no intente
    arrancarlo dos veces.

    El texto del aviso permanente esta redactado con cuidado. Va a quedarse
    fijo en la bandeja de la persona mientras use la aplicacion, asi que dice
    que hace y por que esta ahi, en lugar de un "ejecutandose en segundo
    plano" que solo genera desconfianza.
    """
    if servicio is None or bucle is None:
        registrar(f"iniciar_vigilancia: no se puede "
                  f"(servicio={servicio is not None}, bucle={bucle is not None})")
        return False

    async def arrancar():
        try:
            await servicio.start_foreground_service(
                id=AVISO_DE_VIGILANCIA,
                title="SyncTask CUN",
                body="Atento a los cambios de su equipo",
                channel_id=CANAL_VIGILANCIA,
                foreground_service_types=[TIPO_DE_SERVICIO],
            )
            registrar("start_foreground_service: OK  <-- el aviso permanente "
                      "deberia verse ya en la bandeja")
        except Exception as error:  # noqa: BLE001
            # Si el telefono lo rechaza, la aplicacion sigue funcionando
            # exactamente como antes: con avisos dentro de la pantalla. Pero
            # ahora queda escrito por que.
            _fallo("start_foreground_service", error)

        # Comprueba si el aviso llego de verdad a la bandeja. Es la diferencia
        # entre "se pidio" y "esta puesto", que es justo lo que no sabiamos.
        try:
            activas = await servicio.get_active_notifications()
            ids = [getattr(a, "id", "?") for a in (activas or [])]
            registrar(f"get_active_notifications: {len(ids)} activa(s), ids={ids}")
        except Exception as error:  # noqa: BLE001
            _fallo("get_active_notifications", error)

    try:
        asyncio.run_coroutine_threadsafe(arrancar(), bucle)
        return True
    except Exception as error:  # noqa: BLE001
        _fallo("iniciar_vigilancia/programar", error)
        return False


def detener_vigilancia(bucle, servicio):
    """Retira el aviso permanente y deja de mantener vivo el proceso."""
    if servicio is None or bucle is None:
        return

    async def detener():
        try:
            await servicio.stop_foreground_service()
        except Exception:  # noqa: BLE001
            pass

    try:
        asyncio.run_coroutine_threadsafe(detener(), bucle)
    except Exception:  # noqa: BLE001
        pass
