"""Verifica que los fallos tecnicos nunca lleguen a la pantalla.

    cd ~/synctask && python3 prueba_mensajes.py

De donde sale esta prueba
-------------------------
De usar la aplicacion como lo haria cualquiera. Escribiendo correo y
contraseña con la base de datos inalcanzable, la pantalla respondia:

    No se pudo verificar la cuenta: Failed to connect to proxy URL:
    "http://127.0.0.1:46023"

Eso no le dice nada a quien usa la aplicacion y ademas publica detalles
internos del servidor. Habia diecisiete sitios interpolando la excepcion
cruda en el texto visible.

Lo que se comprueba aqui es la regla que los sustituyo: los errores que
escribe este proyecto estan redactados para leerse y salen tal cual;
cualquier otro se resume en una frase util y el detalle tecnico se manda al
diario del servicio.

La comprobacion que de verdad importa es la ultima: que ningun mensaje
mostrado contenga rastros tecnicos. Esa es la que fallaria si alguien
volviera a interpolar una excepcion en una cadena.
"""

import os
import sys

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from botocore.exceptions import ClientError, EndpointConnectionError  # noqa: E402

import main as app  # noqa: E402
from modulos import acceso_google, proyectos, sesion, tareas  # noqa: E402

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


def cliente(codigo, mensaje="algo"):
    return ClientError({"Error": {"Code": codigo, "Message": mensaje}}, "Query")


# --- Los errores propios se muestran tal cual -----------------------------

print("\n--- Errores redactados por el proyecto ---")

for excepcion, nombre in (
    (sesion.ErrorDeRegistro("Ese correo ya tiene cuenta."), "ErrorDeRegistro"),
    (acceso_google.ErrorDeGoogle("Google no verifico el correo."), "ErrorDeGoogle"),
    (proyectos.ErrorDeCodigo("Ese codigo no existe."), "ErrorDeCodigo"),
    (tareas.ErrorDePermiso("Solo el autor puede borrarla."), "ErrorDePermiso"),
):
    comprobar(f"{nombre} se muestra literal",
              app.explicar(excepcion) == str(excepcion),
              app.explicar(excepcion))


# --- Los fallos de infraestructura se traducen ----------------------------

print("\n--- Fallos de infraestructura ---")

red = app.explicar(EndpointConnectionError(
    endpoint_url="https://dynamodb.us-east-1.amazonaws.com"))
comprobar("Sin conexion con DynamoDB -> mensaje sobre la conexion",
          "conexion" in red.lower() and "endpoint" not in red.lower(), red)

proxy = app.explicar(Exception(
    'Failed to connect to proxy URL: "http://127.0.0.1:46023"'))
comprobar("El fallo exacto que aparecio en pantalla ya no se muestra",
          "127.0.0.1" not in proxy and "proxy" not in proxy.lower(), proxy)

caducado = app.explicar(cliente("ExpiredTokenException", "token caducado"))
comprobar("Credenciales caducadas -> se pide avisar al equipo",
          "equipo" in caducado.lower(), caducado)

saturado = app.explicar(cliente("ThrottlingException"))
comprobar("Base de datos saturada -> se pide reintentar",
          "momento" in saturado.lower() or "ocupado" in saturado.lower(),
          saturado)

raro = app.explicar(ValueError("algo raro paso en la linea 42"))
comprobar("Un error cualquiera no filtra su texto",
          "linea 42" not in raro, raro)

comprobar("Sin excepcion tampoco revienta",
          isinstance(app.explicar(None), str))


# --- La comprobacion de fondo ---------------------------------------------

print("\n--- Ningun rastro tecnico en lo que se muestra ---")

SOSPECHOSOS = ("Traceback", "Error occurred", "127.0.0.1", "http://",
               "https://", "botocore", "boto3", "Exception", "None",
               "dynamodb", "endpoint", "Errno", "proxy")

TECNICOS = [
    EndpointConnectionError(endpoint_url="https://dynamodb.us-east-1.amazonaws.com"),
    Exception('Failed to connect to proxy URL: "http://127.0.0.1:46023"'),
    cliente("ExpiredTokenException"),
    cliente("ThrottlingException"),
    cliente("AccessDeniedException"),
    cliente("UnrecognizedClientException"),
    ConnectionResetError(104, "Connection reset by peer"),
    TimeoutError("se acabo el tiempo"),
    KeyError("sal"),
    ValueError("x"),
    RuntimeError("TimeoutException after 0:00:10 ... Window(2).to_front"),
]

sucios = []
for excepcion in TECNICOS:
    mensaje = app.explicar(excepcion)
    for rastro in SOSPECHOSOS:
        if rastro.lower() in mensaje.lower():
            sucios.append(f"{type(excepcion).__name__} -> {mensaje!r} ({rastro})")

comprobar(f"Los {len(TECNICOS)} fallos tecnicos salen limpios",
          not sucios, "\n         ".join(sucios))

# Autocomprobacion: si el detector de rastros no detectara nada, la linea de
# arriba pasaria sin comprobar nada. Se le da un mensaje que SI esta sucio y
# tiene que cazarlo.
sucio_a_proposito = 'No se pudo: Failed to connect to proxy URL "http://127.0.0.1:1"'
caza = any(r.lower() in sucio_a_proposito.lower() for r in SOSPECHOSOS)
comprobar("AUTOCOMPROBACION: el detector caza un mensaje sucio de verdad",
          caza, "si esto falla, la comprobacion de arriba no prueba nada")


# --- Que no vuelva a colarse una excepcion cruda --------------------------

print("\n--- El codigo fuente no interpola excepciones ---")

import pathlib  # noqa: E402

fuente = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf-8")
comprobar("main.py no vuelve a escribir {fallo} sin traducir",
          "{fallo}" not in fuente,
          "usar siempre {explicar(fallo)} en los mensajes visibles")

print()
fallidas = _resultados.count(False)
print(f"{len(_resultados) - fallidas}/{len(_resultados)} comprobaciones correctas")
sys.exit(1 if fallidas else 0)
