"""Registro, autenticacion y perfil de usuario (REQ-08, REQ-11).

Decisiones de diseño relevantes:

1. Identidad. El usuario se almacena en USUARIO#{uuid} y un elemento espejo
   CORREO#{correo} resuelve el correo a ese identificador. El espejo se crea
   en la misma transaccion con attribute_not_exists, de modo que la unicidad
   del correo queda garantizada por la base de datos y no por una consulta
   previa sujeta a condiciones de carrera. Es el mismo patron que usa
   repositorio.crear_proyecto para el codigo de acceso.

2. Minimizacion. Se guardan unicamente nombre, correo institucional y la
   contraseña derivada, conforme al principio de finalidad declarado en el
   aviso de privacidad. No se solicita documento, telefono ni ubicacion.

3. Contraseñas. Se deriva con scrypt y sal aleatoria por usuario. La
   contraseña en texto claro no se escribe nunca en la base de datos ni en
   los registros de la aplicacion (REQ-11).

4. Autorizacion. El registro se rechaza si el titular no otorga autorizacion
   expresa para el tratamiento de sus datos. La validacion vive aqui y no
   solo en la interfaz, para que sea verificable con una prueba automatizada
   (REQ-08, Ley 1581 de 2012).
"""

import hashlib
import hmac
import re
import secrets
import uuid

from botocore.exceptions import ClientError

from datos.cliente import obtener_tabla, NOMBRE_TABLA
from datos.repositorio import _ahora

# Parametros de derivacion. Se almacenan junto al hash para poder
# endurecerlos en el futuro sin invalidar las cuentas existentes.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LONGITUD = 64
LONGITUD_SAL = 16

LONGITUD_MINIMA_CLAVE = 8

PATRON_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Dominios admitidos para registrarse. Una lista vacia desactiva la
# comprobacion y deja entrar cualquier correo.
#
# Estuvo en ("cun.edu.co",) y se abrio a proposito. El motivo: la aplicacion
# se sustenta y se prueba con personas que no siempre tienen a mano el correo
# institucional, y cerrar el registro a un dominio impedia que un jurado o un
# companero de otro programa pudiera probarla en el momento.
#
# Lo que se pierde al abrirlo conviene decirlo, no esconderlo: el correo ya no
# acredita pertenencia a la institucion. Lo que NO se pierde es el control de
# quien ve que: a un proyecto solo se entra con su codigo de invitacion, asi
# que tener cuenta no da acceso a ningun tablero ajeno. El dominio era una
# comodidad, el codigo de invitacion es la medida que de verdad protege.
#
# Para volver a cerrarlo basta con poner aqui los dominios; no hay que tocar
# ninguna otra linea.
DOMINIOS_ADMITIDOS = ()


class ErrorDeRegistro(Exception):
    """El registro no cumple una condicion exigida."""


def _normalizar_correo(correo):
    return correo.strip().lower()


def _derivar(clave, sal):
    """Deriva la contraseña con scrypt y devuelve el resultado en hexadecimal."""
    return hashlib.scrypt(
        clave.encode("utf-8"),
        salt=sal,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LONGITUD,
    ).hex()


def _validar_entrada(nombre, correo, clave, autoriza_tratamiento):
    """Comprueba las condiciones de registro antes de tocar la base de datos."""
    if not nombre or not nombre.strip():
        raise ErrorDeRegistro("El nombre es obligatorio.")

    if not PATRON_CORREO.match(correo):
        raise ErrorDeRegistro("El correo no tiene un formato valido.")

    if DOMINIOS_ADMITIDOS and not correo.endswith(tuple(
        f"@{dominio}" for dominio in DOMINIOS_ADMITIDOS
    )):
        admitidos = ", ".join(DOMINIOS_ADMITIDOS)
        raise ErrorDeRegistro(
            f"Debe registrarse con su correo institucional ({admitidos})."
        )

    if len(clave or "") < LONGITUD_MINIMA_CLAVE:
        raise ErrorDeRegistro(
            f"La contraseña debe tener al menos {LONGITUD_MINIMA_CLAVE} caracteres."
        )

    # REQ-08: sin autorizacion expresa del titular no se crea la cuenta.
    if not autoriza_tratamiento:
        raise ErrorDeRegistro(
            "Para crear la cuenta debe autorizar el tratamiento de sus datos "
            "personales en los terminos del aviso de privacidad."
        )


def registrar_usuario(nombre, correo, clave, autoriza_tratamiento):
    """Crea una cuenta y su espejo de correo en una sola transaccion (REQ-08).

    Devuelve el perfil creado, sin la contraseña derivada ni la sal.
    Lanza ErrorDeRegistro si la entrada no es valida, si falta la
    autorizacion del titular o si el correo ya esta registrado.
    """
    correo = _normalizar_correo(correo)
    _validar_entrada(nombre, correo, clave, autoriza_tratamiento)

    tabla = obtener_tabla()
    cliente = tabla.meta.client

    id_usuario = str(uuid.uuid4())
    sal = secrets.token_bytes(LONGITUD_SAL)
    momento = _ahora()

    perfil = {
        "PK": f"USUARIO#{id_usuario}",
        "SK": "PERFIL",
        "id_usuario": id_usuario,
        "nombre": nombre.strip(),
        "correo": correo,
        "clave_derivada": _derivar(clave, sal),
        "sal": sal.hex(),
        "parametros_kdf": {
            "algoritmo": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "dklen": SCRYPT_LONGITUD,
        },
        "autoriza_tratamiento": True,
        "fecha_autorizacion": momento,
        "fecha_creacion": momento,
    }

    espejo = {
        "PK": f"CORREO#{correo}",
        "SK": "USUARIO",
        "id_usuario": id_usuario,
        "fecha_creacion": momento,
    }

    try:
        cliente.transact_write_items(
            TransactItems=[
                {"Put": {
                    "TableName": NOMBRE_TABLA,
                    "Item": perfil,
                    "ConditionExpression": "attribute_not_exists(PK)",
                }},
                {"Put": {
                    "TableName": NOMBRE_TABLA,
                    "Item": espejo,
                    "ConditionExpression": "attribute_not_exists(PK)",
                }},
            ]
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            raise ErrorDeRegistro("Ese correo ya tiene una cuenta registrada.")
        raise

    return _perfil_publico(perfil)


def _perfil_publico(elemento):
    """Devuelve el perfil sin material sensible, apto para la interfaz."""
    return {
        "id_usuario": elemento["id_usuario"],
        "nombre": elemento["nombre"],
        "correo": elemento["correo"],
        "fecha_creacion": elemento["fecha_creacion"],
    }


def _buscar_por_correo(correo):
    """Resuelve un correo al elemento de perfil completo, o None."""
    tabla = obtener_tabla()

    espejo = tabla.get_item(Key={"PK": f"CORREO#{correo}", "SK": "USUARIO"})
    if "Item" not in espejo:
        return None

    id_usuario = espejo["Item"]["id_usuario"]
    perfil = tabla.get_item(Key={"PK": f"USUARIO#{id_usuario}", "SK": "PERFIL"})
    return perfil.get("Item")


def autenticar(correo, clave):
    """Verifica las credenciales y devuelve el perfil publico, o None.

    La comparacion usa hmac.compare_digest para no filtrar informacion por
    el tiempo de respuesta. Se devuelve None tanto si el correo no existe
    como si la contraseña no coincide, de modo que la interfaz no revele
    cual de los dos campos fallo.
    """
    elemento = _buscar_por_correo(_normalizar_correo(correo))
    if elemento is None:
        return None

    # Una cuenta creada con Google no tiene contraseña derivada ni sal. Sin
    # esta guarda, el acceso de abajo levantaria KeyError y la interfaz diria
    # "no se pudo verificar la cuenta", que es un mensaje de fallo tecnico
    # para algo que no es un fallo.
    #
    # Se devuelve None, el mismo resultado que una contraseña equivocada, y no
    # un mensaje del tipo "esa cuenta entra con Google": decirlo confirmaria
    # que ese correo tiene cuenta, que es justo lo que el mensaje unico de mas
    # abajo esta evitando. La salida para esa persona es el boton de Google,
    # que esta en la misma pantalla.
    if not elemento.get("clave_derivada") or not elemento.get("sal"):
        return None

    parametros = elemento.get("parametros_kdf", {})
    derivada = hashlib.scrypt(
        (clave or "").encode("utf-8"),
        salt=bytes.fromhex(elemento["sal"]),
        n=int(parametros.get("n", SCRYPT_N)),
        r=int(parametros.get("r", SCRYPT_R)),
        p=int(parametros.get("p", SCRYPT_P)),
        dklen=int(parametros.get("dklen", SCRYPT_LONGITUD)),
    ).hex()

    if not hmac.compare_digest(derivada, elemento["clave_derivada"]):
        return None

    return _perfil_publico(elemento)


def _crear_desde_google(nombre, correo, google_sub):
    """Crea una cuenta sin contraseña a partir de un perfil de Google.

    Es la misma transaccion de dos elementos que `registrar_usuario`: perfil y
    espejo de correo, ambos con attribute_not_exists. Dos personas no pueden
    quedarse con el mismo correo aunque pulsen el boton a la vez, porque la
    unicidad la impone la base de datos y no una consulta previa.

    No se guarda clave_derivada ni sal, y eso es intencional: una cuenta de
    Google no tiene contraseña en esta aplicacion. `autenticar` ya sabe
    reconocer ese caso.
    """
    tabla = obtener_tabla()
    cliente = tabla.meta.client

    id_usuario = str(uuid.uuid4())
    momento = _ahora()

    perfil = {
        "PK": f"USUARIO#{id_usuario}",
        "SK": "PERFIL",
        "id_usuario": id_usuario,
        "nombre": nombre.strip(),
        "correo": correo,
        "origen": "google",
        "google_sub": google_sub,
        # Entrar con Google es un acto expreso del titular sobre su propia
        # cuenta, y la pantalla de ingreso enlaza el aviso de privacidad antes
        # de ofrecer el boton. Queda registrado igual que en el registro
        # normal para que la trazabilidad de la Ley 1581 no dependa de por
        # donde entro la persona.
        "autoriza_tratamiento": True,
        "fecha_autorizacion": momento,
        "fecha_creacion": momento,
    }

    espejo = {
        "PK": f"CORREO#{correo}",
        "SK": "USUARIO",
        "id_usuario": id_usuario,
        "fecha_creacion": momento,
    }

    cliente.transact_write_items(
        TransactItems=[
            {"Put": {
                "TableName": NOMBRE_TABLA,
                "Item": perfil,
                "ConditionExpression": "attribute_not_exists(PK)",
            }},
            {"Put": {
                "TableName": NOMBRE_TABLA,
                "Item": espejo,
                "ConditionExpression": "attribute_not_exists(PK)",
            }},
        ]
    )

    return _perfil_publico(perfil)


def _anotar_google(id_usuario, google_sub):
    """Deja constancia del identificador de Google en una cuenta existente.

    Es informativo: el enlace se hace por correo, no por este campo. Sirve
    para poder auditar despues quien entro por Google, y no se deja fallar la
    sesion si la escritura no sale.
    """
    try:
        obtener_tabla().update_item(
            Key={"PK": f"USUARIO#{id_usuario}", "SK": "PERFIL"},
            UpdateExpression="SET google_sub = :s",
            ConditionExpression="attribute_not_exists(google_sub)",
            ExpressionAttributeValues={":s": google_sub},
        )
    except Exception:  # noqa: BLE001
        pass


def acceder_con_google(perfil_google):
    """Resuelve un perfil de Google a una cuenta de SyncTask.

    `perfil_google` es lo que devuelve acceso_google.perfil_de(), que ya
    comprobo que el correo viene verificado. Aqui se da por hecho.

    La politica de enlace, que es la decision de diseño de todo esto: si el
    correo ya tiene cuenta, se entra a ESA cuenta, no se crea una segunda. Es
    lo que la persona espera —se registro con su correo y entra con su
    correo— y es seguro precisamente porque Google solo marca como verificado
    el correo cuyo buzon controla el titular. Enlazar por correo sin esa
    comprobacion seria regalar cuentas ajenas a quien declare el correo.

    Limitacion conocida, por si aparece: el enlace es por correo. Si alguien
    cambia la direccion de su cuenta de Google, la proxima vez entrara a una
    cuenta nueva en lugar de a la suya. Para el alcance del curso no compensa
    resolverlo; para resolverlo habria que buscar primero por google_sub.
    """
    correo = _normalizar_correo(perfil_google["correo"])
    sub = perfil_google["sub"]

    elemento = _buscar_por_correo(correo)
    if elemento is not None:
        _anotar_google(elemento["id_usuario"], sub)
        return _perfil_publico(elemento)

    try:
        return _crear_desde_google(perfil_google["nombre"], correo, sub)
    except ClientError as error:
        if error.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        # Carrera: alguien creo esa cuenta entre la consulta y la escritura.
        # No es un error para quien esta ingresando; la cuenta que queria ya
        # existe, asi que se entra a ella.
        elemento = _buscar_por_correo(correo)
        if elemento is None:
            raise ErrorDeRegistro(
                "No se pudo crear la cuenta con Google. Intentelo de nuevo."
            )
        return _perfil_publico(elemento)


def obtener_usuario(id_usuario):
    """Devuelve el perfil publico de un usuario por su identificador."""
    tabla = obtener_tabla()
    respuesta = tabla.get_item(Key={"PK": f"USUARIO#{id_usuario}", "SK": "PERFIL"})
    elemento = respuesta.get("Item")
    return _perfil_publico(elemento) if elemento else None