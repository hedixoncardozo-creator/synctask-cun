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

Limites declarados, porque conviene nombrarlos antes de que los encuentre otro:

- El testigo se guarda sin cifrar. En Android ese almacenamiento es privado de
  la aplicacion, pero un dispositivo con acceso de superusuario podria leerlo.
  La mitigacion seria el almacen de credenciales del sistema operativo, que
  excede el alcance de esta entrega.
- Caduca a los treinta dias y se revoca al cerrar sesion, pero no hay revocacion
  remota: si alguien pierde el telefono, hoy no puede invalidar esa sesion desde
  otro dispositivo.
"""

from datos import repositorio

CLAVE = "synctask.testigo"


def recordar(preferencias, id_usuario):
    """Emite un testigo y lo guarda en el dispositivo. Devuelve el testigo.

    Si el almacenamiento del dispositivo falla, la sesion sigue siendo valida
    en esta ejecucion: no poder recordarla es una molestia, no un motivo para
    impedir el ingreso.
    """
    testigo = repositorio.crear_testigo(id_usuario)
    try:
        preferencias.set(CLAVE, testigo)
    except Exception:
        return testigo
    return testigo


def recuperar(preferencias):
    """Devuelve el perfil del usuario recordado, o None.

    Limpia el testigo guardado cuando ya no sirve, para no reintentar la misma
    consulta inutil en cada apertura.
    """
    try:
        testigo = preferencias.get(CLAVE)
    except Exception:
        return None
    if not testigo:
        return None

    try:
        id_usuario = repositorio.usuario_de_testigo(testigo)
    except Exception:
        # Un fallo de red al arrancar no debe dejar la aplicacion bloqueada:
        # se cae con elegancia a la pantalla de ingreso.
        return None

    if not id_usuario:
        olvidar(preferencias, revocar=False)
        return None

    try:
        return repositorio.obtener_usuario(id_usuario)
    except Exception:
        return None


def olvidar(preferencias, revocar=True):
    """Borra el testigo del dispositivo y, por omision, tambien de la tabla."""
    testigo = None
    try:
        testigo = preferencias.get(CLAVE)
    except Exception:
        pass

    if revocar and testigo:
        try:
            repositorio.revocar_testigo(testigo)
        except Exception:
            pass

    try:
        preferencias.remove(CLAVE)
    except Exception:
        pass
