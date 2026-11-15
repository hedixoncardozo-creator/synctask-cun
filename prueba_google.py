"""Verifica el acceso con cuenta de Google (REQ-08).

No toca DynamoDB ni la red: sustituye la tabla por un doble y le entrega al
modulo perfiles de Google escritos a mano.

    cd ~/synctask && python3 prueba_google.py

Que se comprueba y por que
--------------------------
La decision de diseño de todo esto es enlazar la cuenta **por correo
verificado**: si el correo que llega de Google ya tiene cuenta en SyncTask, se
entra a esa cuenta en lugar de crear una segunda. Es comodo y es lo que la
persona espera, pero descansa entero sobre una sola bandera: `email_verified`.
Si esa comprobacion se cayera, cualquiera que declarara un correo ajeno
entraria a la cuenta de otra persona. Por eso la mitad de esta prueba la
rodea, incluida la trampa de la cadena "false", que en Python es verdadera y
anularia la comprobacion sin levantar ningun error.

La otra mitad cubre el daño colateral: una cuenta creada con Google no tiene
contraseña ni sal, y `autenticar` accedia a esos campos directamente. Sin
proteccion, escribir cualquier contraseña contra una cuenta de Google
levantaba KeyError y la interfaz respondia "no se pudo verificar la cuenta",
un mensaje de fallo tecnico para algo que no es un fallo.
"""

import os
import sys

from botocore.exceptions import ClientError

from modulos import acceso_google, sesion

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


# ---------------------------------------------------------------------------
# Doble de la tabla
# ---------------------------------------------------------------------------


class ClienteFalso:
    def __init__(self, tabla):
        self.tabla = tabla

    def transact_write_items(self, TransactItems):
        # Se valida TODO antes de escribir NADA: una transaccion que escribe a
        # medias no imitaria a DynamoDB y haria pasar pruebas que en el
        # servidor fallarian.
        nuevos = []
        for accion in TransactItems:
            elemento = accion["Put"]["Item"]
            clave = (elemento["PK"], elemento["SK"])
            if ("attribute_not_exists(PK)" in accion["Put"].get(
                    "ConditionExpression", "") and clave in self.tabla.datos):
                raise ClientError(
                    {"Error": {"Code": "TransactionCanceledException",
                               "Message": "la condicion no se cumplio"}},
                    "TransactWriteItems",
                )
            nuevos.append((clave, elemento))
        for clave, elemento in nuevos:
            self.tabla.datos[clave] = dict(elemento)
        self.tabla.escrituras += 1


class Meta:
    def __init__(self, cliente):
        self.client = cliente


class TablaFalsa:
    def __init__(self):
        self.datos = {}
        self.escrituras = 0
        self.lecturas = 0
        self.meta = Meta(ClienteFalso(self))

    def get_item(self, Key):
        self.lecturas += 1
        elemento = self.datos.get((Key["PK"], Key["SK"]))
        return {"Item": dict(elemento)} if elemento else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues,
                    ConditionExpression=None):
        clave = (Key["PK"], Key["SK"])
        elemento = self.datos.get(clave)
        if elemento is None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException",
                           "Message": "no existe"}}, "UpdateItem")
        campo = UpdateExpression.split("SET", 1)[1].split("=")[0].strip()
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if campo in elemento:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException",
                               "Message": "ya existe"}}, "UpdateItem")
        elemento[campo] = list(ExpressionAttributeValues.values())[0]


def tabla_nueva():
    tabla = TablaFalsa()
    sesion.obtener_tabla = lambda: tabla
    return tabla


# ---------------------------------------------------------------------------
# 1. Validacion del perfil que entrega Google
# ---------------------------------------------------------------------------

print("\n--- Perfil de Google ---")

BUENO = {"sub": "109", "email": "ana@gmail.com", "email_verified": True,
         "name": "Ana Nieto"}


def rechaza(perfil):
    """Devuelve el mensaje si lo rechaza, o None si lo acepto."""
    try:
        acceso_google.perfil_de(perfil)
    except acceso_google.ErrorDeGoogle as fallo:
        return str(fallo)
    return None


perfil = acceso_google.perfil_de(BUENO)
comprobar("Un perfil verificado se traduce al vocabulario de la aplicacion",
          perfil == {"sub": "109", "correo": "ana@gmail.com",
                     "nombre": "Ana Nieto"},
          str(perfil))

comprobar("Se rechaza el correo que Google NO da por verificado",
          rechaza({**BUENO, "email_verified": False}) is not None)

comprobar("Se rechaza si email_verified ni siquiera viene",
          rechaza({k: v for k, v in BUENO.items() if k != "email_verified"})
          is not None)

# La trampa: bool("false") es True. Si la comprobacion usara bool() en lugar
# de comparar el texto, esta linea pasaria y el modulo quedaria sin defensa.
comprobar('Se rechaza la CADENA "false", que en Python es verdadera',
          rechaza({**BUENO, "email_verified": "false"}) is not None)

comprobar('Se acepta la cadena "true", porque no toda biblioteca manda booleano',
          rechaza({**BUENO, "email_verified": "true"}) is None)

comprobar("Se rechaza un perfil sin correo",
          rechaza({**BUENO, "email": ""}) is not None)

comprobar("Se rechaza un perfil sin identificador de cuenta",
          rechaza({**BUENO, "sub": ""}) is not None)

comprobar("Se rechaza un perfil vacio", rechaza(None) is not None)

comprobar("El correo se normaliza a minusculas y sin espacios",
          acceso_google.perfil_de(
              {**BUENO, "email": "  ANA@Gmail.COM "})["correo"]
          == "ana@gmail.com")

comprobar("Sin nombre, se usa la parte anterior a la arroba",
          acceso_google.perfil_de(
              {**BUENO, "name": ""})["nombre"] == "ana")

# Autocomprobacion: si `rechaza` devolviera siempre algo, las cinco lineas de
# arriba pasarian sin comprobar nada. Esta linea obliga a que el detector sepa
# distinguir, y es la que hace que las otras signifiquen algo.
comprobar("AUTOCOMPROBACION: el detector acepta lo que debe aceptar",
          rechaza(BUENO) is None,
          "si esto falla, los rechazos de arriba no prueban nada")


# ---------------------------------------------------------------------------
# 2. Configuracion por entorno
# ---------------------------------------------------------------------------

print("\n--- Configuracion ---")

for v in (acceso_google.VARIABLE_ID, acceso_google.VARIABLE_SECRETO,
          acceso_google.VARIABLE_REDIRECCION):
    os.environ.pop(v, None)

comprobar("Sin variables de entorno, el acceso se declara no configurado",
          acceso_google.configurado() is False)

mensaje = ""
try:
    acceso_google.crear_proveedor()
except acceso_google.ErrorDeGoogle as fallo:
    mensaje = str(fallo)

comprobar("El error dice exactamente que variables faltan",
          all(v in mensaje for v in (acceso_google.VARIABLE_ID,
                                     acceso_google.VARIABLE_SECRETO,
                                     acceso_google.VARIABLE_REDIRECCION)),
          mensaje)

os.environ[acceso_google.VARIABLE_ID] = "123.apps.googleusercontent.com"
os.environ[acceso_google.VARIABLE_SECRETO] = "secreto-de-prueba"
os.environ[acceso_google.VARIABLE_REDIRECCION] = \
    "https://52-54-228-167.nip.io/oauth_callback"

comprobar("Con las tres variables, el acceso se declara configurado",
          acceso_google.configurado() is True)

proveedor = acceso_google.crear_proveedor()
comprobar("El proveedor pide los permisos de correo y de perfil",
          any("userinfo.email" in s for s in proveedor.user_scopes)
          and any("userinfo.profile" in s for s in proveedor.user_scopes))

comprobar("El proveedor apunta al callback del propio servidor",
          proveedor.redirect_url.endswith("/oauth_callback"))


# ---------------------------------------------------------------------------
# 3. Enlace de la cuenta contra la tabla
# ---------------------------------------------------------------------------

print("\n--- Enlace de la cuenta ---")

tabla = tabla_nueva()
nueva = sesion.acceder_con_google(
    {"sub": "109", "correo": "ana@gmail.com", "nombre": "Ana Nieto"})

comprobar("Un correo desconocido crea una cuenta",
          nueva and nueva["correo"] == "ana@gmail.com"
          and nueva["nombre"] == "Ana Nieto", str(nueva))

guardado = tabla.datos[(f"USUARIO#{nueva['id_usuario']}", "PERFIL")]

comprobar("La cuenta de Google se guarda SIN contraseña derivada ni sal",
          "clave_derivada" not in guardado and "sal" not in guardado,
          str(sorted(guardado)))

comprobar("Queda registrada la autorizacion del titular (Ley 1581)",
          guardado.get("autoriza_tratamiento") is True
          and bool(guardado.get("fecha_autorizacion")))

comprobar("Queda el espejo del correo, que es lo que garantiza la unicidad",
          (f"CORREO#ana@gmail.com", "USUARIO") in tabla.datos)

# Segundo ingreso con la misma cuenta: NO debe crear otra.
escrituras_antes = tabla.escrituras
otra_vez = sesion.acceder_con_google(
    {"sub": "109", "correo": "ana@gmail.com", "nombre": "Ana Nieto"})

comprobar("Volver a entrar con Google devuelve LA MISMA cuenta",
          otra_vez["id_usuario"] == nueva["id_usuario"])

comprobar("Volver a entrar no crea una cuenta nueva",
          tabla.escrituras == escrituras_antes,
          f"escrituras antes={escrituras_antes} despues={tabla.escrituras}")


# El caso que de verdad importa: cuenta creada con contraseña, y la misma
# persona entra despues con Google.
print("\n--- Enlace con una cuenta que ya tenia contraseña ---")

tabla = tabla_nueva()
con_clave = sesion.registrar_usuario(
    "Luis Mancheno", "luis@tesamerica.com.co", "clave-larga-123", True)

enlazada = sesion.acceder_con_google(
    {"sub": "777", "correo": "luis@tesamerica.com.co", "nombre": "Luis M."})

comprobar("Entrar con Google lleva a la cuenta que ya existia, no a una nueva",
          enlazada["id_usuario"] == con_clave["id_usuario"],
          f"existente={con_clave['id_usuario']} google={enlazada['id_usuario']}")

comprobar("No se pisa el nombre con el que la persona se habia registrado",
          enlazada["nombre"] == "Luis Mancheno")

comprobar("Se anota el identificador de Google en la cuenta existente",
          tabla.datos[(f"USUARIO#{con_clave['id_usuario']}",
                       "PERFIL")].get("google_sub") == "777")

comprobar("La contraseña de siempre sigue sirviendo despues de enlazar",
          sesion.autenticar("luis@tesamerica.com.co", "clave-larga-123")
          is not None)


# ---------------------------------------------------------------------------
# 4. Daño colateral: el formulario de siempre contra una cuenta de Google
# ---------------------------------------------------------------------------

print("\n--- El formulario clasico contra una cuenta de Google ---")

tabla = tabla_nueva()
sesion.acceder_con_google(
    {"sub": "109", "correo": "ana@gmail.com", "nombre": "Ana Nieto"})

fallo = None
try:
    resultado = sesion.autenticar("ana@gmail.com", "lo-que-sea")
except Exception as error:  # noqa: BLE001
    fallo = error
    resultado = "excepcion"

comprobar("Escribir una contraseña contra una cuenta de Google NO revienta",
          fallo is None, f"{type(fallo).__name__}: {fallo}" if fallo else "")

comprobar("...y responde lo mismo que una contraseña equivocada",
          resultado is None,
          "un mensaje distinto delataria que ese correo tiene cuenta")


# ---------------------------------------------------------------------------
# 5. El registro ya no exige dominio institucional
# ---------------------------------------------------------------------------

print("\n--- Registro con cualquier dominio ---")

tabla = tabla_nueva()
libre = sesion.registrar_usuario("Stiven Soto", "stiven@gmail.com",
                                 "otra-clave-123", True)
comprobar("Se admite un correo de cualquier dominio",
          libre["correo"] == "stiven@gmail.com")

rechazo = None
try:
    sesion.registrar_usuario("Sin arroba", "esto-no-es-correo", "clave-123456",
                             True)
except sesion.ErrorDeRegistro as error:
    rechazo = str(error)

comprobar("Pero se sigue exigiendo que el correo tenga forma de correo",
          rechazo is not None, rechazo or "")

rechazo = None
try:
    sesion.registrar_usuario("Sin permiso", "otro@gmail.com", "clave-123456",
                             False)
except sesion.ErrorDeRegistro as error:
    rechazo = str(error)

comprobar("Y se sigue exigiendo la autorizacion del titular (REQ-08)",
          rechazo is not None, rechazo or "")


# ---------------------------------------------------------------------------

print()
fallidas = _resultados.count(False)
print(f"{len(_resultados) - fallidas}/{len(_resultados)} comprobaciones correctas")
sys.exit(1 if fallidas else 0)
