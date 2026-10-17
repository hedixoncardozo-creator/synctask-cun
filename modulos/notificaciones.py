"""Notificaciones del sistema en Android, sin Firebase (REQ-05, REQ-13).

Se apoya en la extension `flet-notifications`, que envuelve el complemento de
Flutter `flutter_local_notifications`. Es una notificacion *local*: la lanza la
propia aplicacion cuando recibe el aviso de PubSub, no un servidor de push.

De ahi se desprende el limite que conviene tener presente y declarar en el
informe: la aplicacion tiene que estar viva para recibir el aviso. Con la
aplicacion abierta o minimizada llega; con la aplicacion cerrada del todo, no.
Cubrir ese ultimo caso exige un servicio de push —Firebase u otro—, que queda
fuera del alcance de esta entrega.

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
_contador = {"n": 1000}


def crear_servicio():
    """Devuelve el servicio, o None si la extension no esta disponible."""
    if not DISPONIBLE:
        return None
    try:
        return LocalNotifications()
    except Exception:  # noqa: BLE001
        return None


def preparar(bucle, servicio):
    """Inicializa el servicio y pide el permiso de Android 13 o superior.

    Se programa en el bucle de eventos porque la extension es asincrona. No se
    espera el resultado: si el usuario tarda en responder al permiso, la
    aplicacion no debe quedarse esperandolo.
    """
    if servicio is None or bucle is None:
        return

    async def arranque():
        try:
            await servicio.initialize()
        except Exception:  # noqa: BLE001
            return
        try:
            await servicio.request_permissions()
        except Exception:  # noqa: BLE001
            pass

    try:
        asyncio.run_coroutine_threadsafe(arranque(), bucle)
    except Exception:  # noqa: BLE001
        pass


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
        except Exception:  # noqa: BLE001
            pass

    try:
        asyncio.run_coroutine_threadsafe(lanzar(), bucle)
    except Exception:  # noqa: BLE001
        pass
