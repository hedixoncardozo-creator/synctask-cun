"""Verifica que reabrir la aplicacion no vuelva a pedir el ingreso (REQ-11).

El defecto que motiva esta prueba se encontro usando la aplicacion una semana,
no probandola: al salir y volver a entrar aparecia el formulario de ingreso y
un instante despues saltaba al tablero. La sesion nunca se perdia —eso ya
estaba bien— pero lo que la persona percibia era "me esta volviendo a pedir la
contrasena cada vez".

Por eso aqui no se comprueba *donde termina* la aplicacion, que ya funcionaba,
sino *por donde pasa*: que el formulario de ingreso no llegue a pintarse nunca
cuando hay sesion guardada.

No toca DynamoDB ni Flet real: sustituye la resolucion del testigo por un
doble que permite decidir cuando responde, que es justo la ventana de tiempo
donde ocurria el parpadeo.

    cd ~/synctask && python3 prueba_arranque.py
"""

import sys
import warnings

warnings.filterwarnings("ignore")

import flet as ft  # noqa: E402

import main as app  # noqa: E402
from modulos import notificaciones, recordar  # noqa: E402

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


class PubSubFalso:
    def __init__(self):
        self.temas = {}

    def subscribe_topic(self, tema, manejador):
        self.temas.setdefault(tema, []).append(manejador)

    def unsubscribe_topic(self, tema):
        self.temas.pop(tema, None)

    def send_all_on_topic(self, tema, mensaje):
        for m in self.temas.get(tema, []):
            m(tema, mensaje)


class PaginaFalsa:
    def __init__(self, ruta="/"):
        self.pubsub = PubSubFalso()
        self.views = []
        self.route = ruta
        self.dialogos = []
        self.on_route_change = None
        self.on_view_pop = None
        self.on_disconnect = None
        self.on_connect = None
        self.title = None
        self.theme = None
        self.theme_mode = None
        self.web = False
        self.platform = ft.PagePlatform.ANDROID
        # Toda pantalla por la que pasa la aplicacion, en orden. Es el dato
        # que esta prueba existe para mirar.
        self.recorrido = []

    def show_dialog(self, d):
        self.dialogos.append(d)

    def update(self):
        if self.views:
            self.recorrido.append(_nombrar(self.views[-1]))

    def navigate(self, ruta):
        self.route = ruta
        if self.on_route_change:
            self.on_route_change(None)

    def go(self, ruta):
        self.navigate(ruta)


def _nombrar(vista):
    """Identifica una pantalla por lo que contiene, no por su ruta.

    Mira `label` ademas de `value`: los campos de texto llevan su titulo en
    `label`, y sin eso la pantalla de ingreso era indetectable. Ese descuido
    habria hecho que la comprobacion central de esta prueba —"el formulario no
    llego a pintarse"— se cumpliera por vacio, sin demostrar nada. De ahi la
    autocomprobacion del final del archivo.
    """
    textos = []

    def recorrer(c, hondura=0):
        if hondura > 14:
            return
        for atributo in ("value", "label", "hint_text"):
            v = getattr(c, atributo, None)
            if isinstance(v, str) and v:
                textos.append(v)
        if isinstance(getattr(c, "content", None), str):
            textos.append(c.content)
        for atributo in ("content", "controls"):
            hijo = getattr(c, atributo, None)
            if isinstance(hijo, list):
                for x in hijo:
                    recorrer(x, hondura + 1)
            elif hijo is not None and hasattr(hijo, "_c"):
                recorrer(hijo, hondura + 1)

    for c in getattr(vista, "controls", []) or []:
        recorrer(c)

    junto = " | ".join(textos)
    if "Contrasena" in junto and "Ingresar" in junto:
        return "INGRESO"
    if "Repita la contrasena" in junto:
        return "REGISTRO"
    if "MIS PROYECTOS" in junto or "Todavia no pertenece" in junto:
        return "PROYECTOS"
    if any(isinstance(c, ft.ProgressRing) for c in _aplanar(vista)):
        return "ESPERA"
    return "OTRA"


def _aplanar(vista, hondura=0):
    salida = []

    def recorrer(c, h):
        if h > 14:
            return
        salida.append(c)
        for atributo in ("content", "controls"):
            hijo = getattr(c, atributo, None)
            if isinstance(hijo, list):
                for x in hijo:
                    recorrer(x, h + 1)
            elif hijo is not None and hasattr(hijo, "_c"):
                recorrer(hijo, h + 1)

    for c in getattr(vista, "controls", []) or []:
        recorrer(c, 0)
    return salida


class ServicioFalso:
    """Doble del servicio de notificaciones, para ver si se pide la vigilancia."""

    def __init__(self):
        self.arrancado = 0
        self.detenido = 0


def _instalar_dobles():
    """Sustituye la resolucion del testigo por una controlable a mano."""
    pendiente = {}

    def recuperar_falso(bucle, preferencias, al_terminar):
        pendiente["responder"] = al_terminar

    recordar.recuperar = recuperar_falso
    recordar.recordar = lambda *a, **k: "testigo"
    recordar.olvidar = lambda *a, **k: None

    servicio = ServicioFalso()
    notificaciones.crear_servicio = lambda: servicio
    notificaciones.preparar = lambda *a, **k: None
    notificaciones.mostrar = lambda *a, **k: None

    def iniciar(bucle, s):
        servicio.arrancado += 1
        return True

    def detener(bucle, s):
        servicio.detenido += 1

    notificaciones.iniciar_vigilancia = iniciar
    notificaciones.detener_vigilancia = detener
    return pendiente, servicio


ANA = {"id_usuario": "u1", "nombre": "Ana", "correo": "ana@cun.edu.co"}


def main():
    print("\nVerificacion del arranque de la aplicacion")
    print("=" * 64)

    # Antes de afirmar que una pantalla NO aparecio hay que demostrar que se
    # sabria reconocerla. Sin esto, un detector roto convierte la comprobacion
    # central de esta prueba en una que se cumple sola.
    print("\nEl detector sabe distinguir las pantallas")
    estado_vacio = app.Estado()
    estado_vacio.restaurando = False
    pagina_suelta = PaginaFalsa("/")
    comprobar("Reconoce la pantalla de ingreso",
              _nombrar(app.vista_ingreso(pagina_suelta, estado_vacio)) == "INGRESO",
              "si esto falla, 'el ingreso no se pinto' no probaria nada")
    comprobar("Reconoce la pantalla de espera",
              _nombrar(app.vista_restaurando(pagina_suelta, estado_vacio))
              == "ESPERA")

    print("\nAl reabrir con sesion guardada")

    pendiente, servicio = _instalar_dobles()
    page = PaginaFalsa("/")
    app.main(page)

    comprobar("Mientras se resuelve, se muestra la espera",
              _nombrar(page.views[-1]) == "ESPERA",
              f"pantalla actual: {_nombrar(page.views[-1])}")
    comprobar("El servicio de vigilancia NO arranca antes de saber quien es",
              servicio.arrancado == 0)

    pendiente["responder"](ANA)

    comprobar("Termina en la lista de proyectos",
              _nombrar(page.views[-1]) == "PROYECTOS",
              f"pantalla final: {_nombrar(page.views[-1])}")
    comprobar("El formulario de ingreso NO se pinto en ningun momento",
              "INGRESO" not in page.recorrido,
              f"recorrido: {' -> '.join(page.recorrido)}")
    comprobar("La vigilancia arranca al restaurar la sesion",
              servicio.arrancado == 1)

    print("\nAl reabrir sin sesion guardada")

    pendiente, servicio = _instalar_dobles()
    page = PaginaFalsa("/")
    app.main(page)
    comprobar("Tambien se muestra la espera primero",
              _nombrar(page.views[-1]) == "ESPERA")

    pendiente["responder"](None)

    comprobar("Y termina en el formulario de ingreso",
              _nombrar(page.views[-1]) == "INGRESO",
              f"pantalla final: {_nombrar(page.views[-1])}")
    comprobar("Nadie se queda atrapado en la espera",
              _nombrar(page.views[-1]) != "ESPERA",
              "si esto fallara, la aplicacion giraria para siempre")
    comprobar("Sin sesion no se pide la vigilancia", servicio.arrancado == 0)

    print("\nUn enlace directo a un tablero sobrevive a la restauracion")

    pendiente, servicio = _instalar_dobles()
    page = PaginaFalsa("/tablero/p1")
    app.main(page)
    comprobar("No expulsa a la pantalla de ingreso mientras resuelve",
              page.route == "/tablero/p1",
              f"ruta: {page.route} "
              "(antes se perdia el enlace por una carrera con el testigo)")

    print("\nCerrar sesion")

    pendiente, servicio = _instalar_dobles()
    page = PaginaFalsa("/")
    app.main(page)
    pendiente["responder"](ANA)
    estado_actual = None
    for vista in page.views:
        estado_actual = getattr(vista, "data", None) or estado_actual
    app.cerrar_sesion(page, _estado_de(app, page))
    comprobar("Se retira el aviso permanente de la bandeja",
              servicio.detenido == 1,
              "dejarlo seria desconcertante y ademas falso")

    print("\n" + "=" * 64)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    return 0 if exitosas == total else 1


def _estado_de(modulo, page):
    """Recupera el Estado que main() creo para esta pagina.

    main() lo guarda en una clausura, asi que se llega a el por el manejador
    de rutas que si quedo colgado de la pagina.
    """
    manejador = page.on_route_change
    for celda in (manejador.__closure__ or []):
        contenido = celda.cell_contents
        if isinstance(contenido, modulo.Estado):
            return contenido
    raise AssertionError("no se encontro el Estado de la sesion")


if __name__ == "__main__":
    sys.exit(main())
