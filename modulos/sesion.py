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

# Dominio institucional esperado. Se deja como lista para admitir variantes
# sin tocar la logica; una lista vacia desactiva la comprobacion.
DOMINIOS_ADMITIDOS = ("cun.edu.co",)


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


def obtener_usuario(id_usuario):
    """Devuelve el perfil publico de un usuario por su identificador."""
    tabla = obtener_tabla()
    respuesta = tabla.get_item(Key={"PK": f"USUARIO#{id_usuario}", "SK": "PERFIL"})
    elemento = respuesta.get("Item")
    return _perfil_publico(elemento) if elemento else None