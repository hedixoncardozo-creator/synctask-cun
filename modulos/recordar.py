"""Sesion recordada entre aperturas de la aplicacion (REQ-11, REQ-10).

El problema que resuelve es concreto: sin esto, abrir el APK obliga a escribir
correo y contrasena cada vez, y una caida momentanea de la red devuelve al
usuario a la pantalla de ingreso en mitad de su trabajo.

Como funciona. Al ingresar correctamente se emite un testigo —una cadena
aleatoria de 256 bits sin significado propio— que se guarda en el
almacenamiento privado de la aplicacion en el dispositivo. Al abrir de nuevo,
la aplicacion presenta ese testigo y DynamoDB responde a que usuario
corresponde, si es que sigue vigente. La contrasena no se guarda en ningun
momento ni viaja de nuevo.

Por que un testigo y no el identificador del usuario: guardar el identificador
seria mas simple y seria un agujero. Ese valor se puede leer y cambiar en el
dispositivo, de modo que bastaria sustituirlo por el de otra persona para
entrar como ella. Un testigo aleatorio no se puede deducir del identificador
de nadie, y su correspondencia vive solo en la base de datos.

Por que estas funciones no devuelven el valor y ya
---------------------------------------------------
La primera version de este modulo llamaba a `preferencias.get(...)` y usaba el
resultado como si fuera el testigo. No lo es: en Flet 0.86 las tres
operaciones de `SharedPreferences` —`get`, `set` y `remove`— son asincronas y
no existe variante sincrona. Llamadas sin `await` devuelven una corrutina que
nadie ejecuta, de modo que no se guardaba nada y, al recuperar, se consultaba
DynamoDB con la clave `TESTIGO#<coroutine object ...>`. La funcion quedaba
muerta en silencio porque todo estaba envuelto en `try/except`; el unico
rastro eran tres `RuntimeWarning: coroutine ... was never awaited` en el
registro del servicio.

De ahi la forma de este modulo. Toda operacion sobre el dispositivo se
programa en el bucle de eventos, y el resultado se entrega por devolucion de
llamada en lugar de devolverse. Quien llama corre dentro de ese mismo bucle y
no puede esperar un resultado sin bloquearlo entero —y este servidor atiende a
todos los usuarios con un solo bucle, asi que bloquearlo congela la aplicacion
de los demas—.

Por la misma razon, las consultas a DynamoDB, que bloquean, se sacan del bucle
con `run_in_executor` en lugar de ejecutarse dentro.

Limites declarados, porque conviene nombrarlos antes de que los encuentre otro:

- El testigo se guarda sin cifrar. En Android ese almacenamiento es privado de
  la aplicacion, pero un dispositivo con acceso de superusuario podria leerlo.
  La mitigacion seria el almacen de credenciales del sistema operativo, que
  excede el alcance de esta entrega.
- Caduca a los treinta dias y se revoca al cerrar sesion, pero no hay revocacion
  remota: si alguien pierde el telefono, hoy no puede invalidar esa sesion desde
  otro dispositivo.
"""

import asyncio

from datos import repositorio

CLAVE = "synctask.testigo"


def _programar(bucle, construir):
    """Pone una corrutina a correr en el bucle. Devuelve si lo consiguio.

    `construir` es una funcion que devuelve la corrutina, y no la corrutina ya
    construida, para poder cerrarla si no llega a programarse: una corrutina
    creada y nunca ejecutada emite el mismo RuntimeWarning que motivo esta
    reescritura.
    """
    if bucle is None:
        return False
    corrutina = construir()
    try:
        asyncio.run_coroutine_threadsafe(corrutina, bucle)
        return True
    except Exception:  # noqa: BLE001
        corrutina.close()
        return False


def recordar(bucle, preferencias, id_usuario):
    """Emite un testigo y pide que se guarde en el dispositivo.

    Se llama desde el hilo de trabajo del ingreso. `crear_testigo` consulta
    DynamoDB y bloquea, pero ahi bloquear no molesta a nadie. Lo que no se
    puede hacer desde ese hilo es tocar el almacenamiento del dispositivo, que
    vive en el bucle; por eso esa mitad se programa.

    Devuelve el testigo de inmediato, sin esperar a que se guarde: si el
    almacenamiento falla, la sesion sigue siendo valida en esta ejecucion. No
    poder recordarla es una molestia, no un motivo para impedir el ingreso.
    """
    testigo = repositorio.crear_testigo(id_usuario)

    async def guardar():
        try:
            await preferencias.set(CLAVE, testigo)
        except Exception:  # noqa: BLE001
            pass

    _programar(bucle, guardar)
    return testigo


def _resolver(testigo):
    """Traduce un testigo a un perfil. Corre fuera del bucle.

    Devuelve `(perfil, caducado)`. La distincion importa: un fallo de red se
    informa con `caducado=False` para NO borrar el testigo del dispositivo. Si
    se borrara, un solo arranque sin cobertura cerraria la sesion para siempre,
    que es justo lo contrario de lo que este modulo existe para evitar.
    """
    try:
        id_usuario = repositorio.usuario_de_testigo(testigo)
    except Exception:  # noqa: BLE001
        return None, False

    if not id_usuario:
        # La consulta si llego a la base de datos y no hay nadie detras de ese
        # testigo: caducado o revocado. Ese si se borra, para no repetir la
        # misma consulta inutil en cada apertura.
        return None, True

    try:
        return repositorio.obtener_usuario(id_usuario), False
    except Exception:  # noqa: BLE001
        return None, False


def recuperar(bucle, preferencias, al_terminar):
    """Resuelve la sesion recordada y entrega el perfil a `al_terminar`.

    `al_terminar` recibe el perfil del usuario, o None si no hay sesion que
    recordar, y se invoca siempre dentro del bucle, que es donde es seguro
    tocar la interfaz. Se invoca una sola vez, pase lo que pase.
    """

    def avisar(perfil):
        try:
            al_terminar(perfil)
        except Exception:  # noqa: BLE001
            # Un fallo pintando la pantalla no debe quedar como excepcion
            # suelta de una tarea, que no se ve en ningun sitio.
            pass

    async def tarea():
        try:
            testigo = await preferencias.get(CLAVE)
        except Exception:  # noqa: BLE001
            testigo = None

        if not testigo:
            avisar(None)
            return

        try:
            perfil, caducado = await bucle.run_in_executor(
                None, _resolver, testigo)
        except Exception:  # noqa: BLE001
            avisar(None)
            return

        if caducado:
            try:
                await preferencias.remove(CLAVE)
            except Exception:  # noqa: BLE001
                pass

        avisar(perfil)

    if not _programar(bucle, tarea):
        avisar(None)


def olvidar(bucle, preferencias, revocar=True):
    """Borra el testigo del dispositivo y, por omision, tambien de la tabla.

    El orden no es casual: primero se quita del dispositivo y despues se
    revoca en la base de datos. Si se hiciera al reves y la revocacion
    fallara, el telefono se quedaria con una llave que sigue siendo valida.
    """

    async def tarea():
        try:
            testigo = await preferencias.get(CLAVE)
        except Exception:  # noqa: BLE001
            testigo = None

        try:
            await preferencias.remove(CLAVE)
        except Exception:  # noqa: BLE001
            pass

        if revocar and testigo:
            try:
                await bucle.run_in_executor(
                    None, _revocar_en_la_tabla, testigo)
            except Exception:  # noqa: BLE001
                pass

    _programar(bucle, tarea)


def _revocar_en_la_tabla(testigo):
    """Consulta a DynamoDB, fuera del bucle."""
    try:
        repositorio.revocar_testigo(testigo)
    except Exception:  # noqa: BLE001
        pass
