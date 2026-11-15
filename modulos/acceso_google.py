"""Acceso con cuenta de Google (REQ-08).

Que resuelve y que no
---------------------
Esto reemplaza el *paso de autenticacion*, no la sesion. Una vez Google
confirma quien es la persona, se emite el mismo testigo de siempre y el resto
de la aplicacion no se entera de nada: el arranque recordado, la vigilancia y
las notificaciones siguen funcionando igual. Por eso este modulo es pequeno.

Donde ocurre cada paso, que es lo que importa
---------------------------------------------
1. La persona pulsa el boton y el servidor registra un `state` aleatorio.
2. El telefono abre la pagina de Google y la persona escoge su cuenta.
3. Google redirige a `{SERVIDOR}/oauth_callback?code=...&state=...`, que pega
   en el servidor de EC2, no en el telefono.
4. El servidor busca la sesion por ese `state`, canjea el codigo por un token
   usando el secreto del cliente y pide el perfil a Google.
5. Flet cierra sola la vista web del telefono y dispara `page.on_login`.

El detalle que justifica la arquitectura: **el secreto del cliente nunca sale
del servidor**. Viaja por el canal servidor-a-servidor del paso 4 y no esta en
el APK, igual que las credenciales de DynamoDB, que se toman del rol de IAM.
Si la aplicacion estuviera empaquetada entera en el telefono, el secreto
tendria que viajar dentro del APK y cualquiera podria extraerlo.

El otro detalle: el paso 4 encuentra la sesion en memoria porque hay **un solo
proceso** en EC2. Con varios servidores detras de un balanceador, el callback
podria caer en una maquina distinta de la que tiene la sesion abierta y haria
falta compartir ese estado. No es el caso aqui, pero conviene que quede dicho
en el informe antes que alguien proponga escalar horizontalmente.

Configuracion
-------------
Las credenciales se leen del entorno y nunca del repositorio. En el servidor
viven en /etc/synctask/google.env, con permisos 600, y el servicio las carga
con EnvironmentFile. Ver despliegue/synctask.service.

Si falta cualquiera de las tres variables, `configurado()` devuelve False y la
interfaz simplemente no ofrece el boton. Es deliberado: una aplicacion que
muestra un boton que no puede funcionar es peor que una que no lo muestra.
"""

import os

VARIABLE_ID = "GOOGLE_CLIENT_ID"
VARIABLE_SECRETO = "GOOGLE_CLIENT_SECRET"
VARIABLE_REDIRECCION = "GOOGLE_REDIRECT_URL"


class ErrorDeGoogle(Exception):
    """El perfil que devolvio Google no sirve para abrir una sesion."""


def _variable(nombre):
    return (os.environ.get(nombre) or "").strip()


def configurado():
    """Dice si el servidor tiene las tres variables necesarias."""
    return all(_variable(v) for v in
               (VARIABLE_ID, VARIABLE_SECRETO, VARIABLE_REDIRECCION))


def redireccion():
    """La URL de retorno, para poder mostrarla al diagnosticar."""
    return _variable(VARIABLE_REDIRECCION)


def crear_proveedor():
    """Construye el proveedor de Flet con las credenciales del entorno.

    Se construye uno por sesion y no uno a nivel de modulo: el proveedor
    guarda estado de la negociacion y compartirlo entre sesiones mezclaria
    los ingresos de dos personas distintas.
    """
    if not configurado():
        faltan = [v for v in (VARIABLE_ID, VARIABLE_SECRETO, VARIABLE_REDIRECCION)
                  if not _variable(v)]
        raise ErrorDeGoogle(
            "El acceso con Google no esta configurado en el servidor. "
            f"Faltan estas variables: {', '.join(faltan)}."
        )

    from flet.auth.providers import GoogleOAuthProvider

    return GoogleOAuthProvider(
        client_id=_variable(VARIABLE_ID),
        client_secret=_variable(VARIABLE_SECRETO),
        redirect_url=_variable(VARIABLE_REDIRECCION),
    )


def _es_verdadero(valor):
    """Interpreta el campo email_verified venga como venga.

    El punto de extremo de Google devuelve un booleano JSON, pero no todas las
    bibliotecas lo entregan igual y una cadena "false" es verdadera en Python.
    Confundir eso aqui anularia la unica comprobacion de seguridad del modulo,
    asi que se resuelve explicitamente en lugar de confiar en bool().
    """
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() == "true"
    return False


def perfil_de(usuario_google):
    """Valida el perfil de Google y lo traduce al vocabulario de la aplicacion.

    Devuelve {"sub", "correo", "nombre"}. Lanza ErrorDeGoogle con un mensaje
    que se le puede mostrar tal cual a la persona.

    La comprobacion que sostiene todo el diseño es `email_verified`. La cuenta
    se enlaza por correo: si alguien pudiera presentar un correo ajeno sin
    verificar, entraria a la cuenta de otra persona. Google solo marca como
    verificado un correo cuyo buzon controla el titular, de modo que esa
    bandera es exactamente la garantia que hace seguro enlazar por correo. Sin
    ella no se abre sesion, por mucho que el resto del perfil venga completo.
    """
    if not usuario_google:
        raise ErrorDeGoogle("Google no devolvio el perfil de la cuenta.")

    correo = (usuario_google.get("email") or "").strip().lower()
    if not correo:
        raise ErrorDeGoogle(
            "La cuenta de Google no entrego un correo. Revise que haya "
            "aceptado compartir su correo al iniciar sesion."
        )

    if not _es_verdadero(usuario_google.get("email_verified")):
        raise ErrorDeGoogle(
            "Google no da por verificado el correo de esa cuenta, asi que no "
            "se puede usar para ingresar."
        )

    sub = str(usuario_google.get("sub")
              or getattr(usuario_google, "id", "") or "").strip()
    if not sub:
        raise ErrorDeGoogle("Google no entrego un identificador de cuenta.")

    nombre = (usuario_google.get("name") or "").strip()
    if not nombre:
        # Hay cuentas sin nombre de pila configurado. Antes que dejar el
        # perfil vacio, se usa la parte anterior a la arroba, que es lo que
        # la persona reconoce como suyo.
        nombre = correo.split("@", 1)[0]

    return {"sub": sub, "correo": correo, "nombre": nombre}
