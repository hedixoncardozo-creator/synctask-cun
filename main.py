"""SyncTask CUN - aplicacion completa (REQ-01 a REQ-13).

El servicio lo gobierna systemd:

    sudo systemctl restart synctask
    journalctl -u synctask -n 50 --no-pager

Seis decisiones gobiernan este archivo y conviene conocerlas antes de leerlo:

1. El estado de la sesion vive dentro de main(). Flet crea una instancia de
   Page por cada navegador conectado y ejecuta main() una vez por cada una,
   asi que todo lo declarado ahi es privado de esa sesion. No hay ninguna
   variable mutable a nivel de modulo. Si la hubiera, dos usuarios conectados
   compartirian el usuario autenticado: es el defecto de seguridad mas comun
   en aplicaciones servidas por un solo proceso, y el que esta arquitectura
   descarta por construccion.

2. Cada pantalla es una funcion que devuelve un ft.View y no modifica nada
   fuera de su propio arbol de controles. El enrutador es el unico que toca
   page.views. Esto permite construir una pantalla en una prueba sin levantar
   el servidor, que es como se verifico este archivo antes de desplegarlo.

3. El aviso de un cambio no lleva los datos del cambio. Cuando llega un
   evento por PubSub, la pantalla vuelve a consultar DynamoDB en lugar de
   aplicar el contenido del mensaje. Cuesta una consulta de particion unica
   —del orden de milisegundos frente al criterio de tres segundos del REQ-05—
   y en cambio elimina la posibilidad de que dos sesiones muestren estados
   distintos porque una perdio un mensaje.

4. La autorizacion no depende de la navegacion. Toda pantalla que lea datos de
   un proyecto pasa por _exigir_pertenencia, porque una direccion se puede
   escribir a mano y el enrutador no es un control de acceso (REQ-07).

5. Ninguna medida esta en pixeles fijos. El ancho de la columna de contenido
   se reparte sobre una rejilla de doce columnas segun el tamano real de la
   ventana, de modo que la misma pantalla sirve a un telefono de 360 px y a un
   monitor de 1920 sin que nada se salga ni se estire.

6. Toda escritura en DynamoDB ocurre fuera del hilo de la interfaz. Una
   escritura dura decenas o cientos de milisegundos, y con mala cobertura
   mucho mas; si se hiciera en el hilo de la interfaz, la pantalla se quedaria
   congelada sin explicacion y el usuario volveria a pulsar, duplicando la
   operacion. El ayudante ejecutar_fuera_del_hilo() es el que sostiene esa
   regla y a la vez muestra el indicador de espera.
"""

import asyncio
import re
from datetime import date, datetime, timezone

import flet as ft

from modulos import (avisos, comentarios, medicion, notificaciones,
                     proyectos, recordar, sesion, tareas)

# ===========================================================================
# Sistema visual
# ===========================================================================
#
# Una paleta corta y deliberada. El fondo es un gris calido en lugar de blanco
# puro porque el blanco puro sobre una pantalla de telefono a pleno sol cansa
# la vista, y porque deja que las tarjetas blancas se distingan sin necesidad
# de sombras. Hay un solo color de acento: cuando todo puede destacar, nada
# destaca, y el acento se reserva para la accion principal de cada pantalla.


class Tono:
    FONDO = "#FAFAF9"
    SUPERFICIE = "#FFFFFF"
    BORDE = "#E7E5E4"
    BORDE_TENUE = "#F5F5F4"
    TINTA = "#1C1917"
    TINTA_SUAVE = "#78716C"
    TINTA_TENUE = "#A8A29E"
    ACENTO = "#4F46E5"
    ACENTO_FONDO = "#EEF2FF"
    ACENTO_BORDE = "#E0E7FF"
    PELIGRO = "#DC2626"
    PELIGRO_FONDO = "#FEF2F2"
    EXITO = "#15803D"
    AVISO = "#B45309"
    AVISO_FONDO = "#FEF3C7"


ESTADOS = ("Pendiente", "En progreso", "Terminado")

ESTILO_ESTADO = {
    "Pendiente": ("#B45309", "#FEF3C7"),
    "En progreso": ("#1D4ED8", "#DBEAFE"),
    "Terminado": ("#15803D", "#DCFCE7"),
}

COLUMNAS = {"xs": 12, "sm": 10, "md": 8, "lg": 6, "xl": 4}

FILTRO_TODAS = "Todas"
FILTRO_MIAS = "Mias"
FILTRO_VENCIDAS = "Vencidas"

AVISO_DE_PRIVACIDAD = (
    "SyncTask CUN recoge su nombre, su correo institucional y los datos de las "
    "tareas y comentarios que escriba. La contrasena no se almacena: se guarda "
    "unicamente un valor derivado con el que no es posible reconstruirla. La "
    "informacion se usa solo para operar la aplicacion dentro del curso, se "
    "conserva en una base de datos de Amazon Web Services en la region "
    "us-east-1 y no se comparte con terceros. Puede solicitar en cualquier "
    "momento el acceso, la correccion o la supresion de sus datos; al suprimir "
    "la cuenta, sus comentarios se conservan bajo una etiqueta generica para no "
    "romper el hilo de las discusiones. El tratamiento se realiza conforme a la "
    "Ley 1581 de 2012 y requiere su autorizacion expresa."
)


class Estado:
    """Datos de una sola sesion. Nunca se instancia a nivel de modulo."""

    def __init__(self):
        self.usuario = None
        self.id_proyecto = None
        self.proyecto = None
        self.suscrito_a = None
        self.filtro = FILTRO_TODAS
        self.refrescar = None
        self.sin_conexion = False
        self.banda = None
        self.vibracion = None
        self.notificador = None
        self.preferencias = None
        self.portapapeles = None
        # Bucle de eventos de esta sesion. Se captura en main(), que si corre
        # dentro de el, y permite devolver el control desde un hilo trabajador.
        self.bucle = None


# ===========================================================================
# Piezas reutilizables
# ===========================================================================


def iniciales(nombre):
    partes = [p for p in re.split(r"\s+", (nombre or "").strip()) if p]
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def color_de_persona(nombre):
    """Color estable derivado del nombre.

    Deriva del texto y no de un contador, de modo que la misma persona
    conserva su color entre sesiones y entre dispositivos sin que haya que
    guardarlo en ninguna parte.
    """
    paleta = ["#4F46E5", "#0891B2", "#C2410C", "#7C3AED", "#BE185D", "#047857"]
    return paleta[sum(ord(c) for c in (nombre or "?")) % len(paleta)]


def tiempo_relativo(marca):
    """Convierte una marca ISO en algo legible: "hace 5 min"."""
    if not marca:
        return ""
    try:
        momento = datetime.fromisoformat(str(marca))
    except ValueError:
        return str(marca)[:16].replace("T", " ")
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    segundos = (datetime.now(timezone.utc) - momento).total_seconds()
    if segundos < 60:
        return "ahora"
    if segundos < 3600:
        return f"hace {int(segundos // 60)} min"
    if segundos < 86400:
        return f"hace {int(segundos // 3600)} h"
    dias = int(segundos // 86400)
    if dias == 1:
        return "ayer"
    if dias < 7:
        return f"hace {dias} dias"
    return momento.strftime("%d/%m/%Y")


def _fecha_de(tarea):
    texto_fecha = (tarea.get("fecha_limite") or "").strip()
    if not texto_fecha:
        return None
    try:
        return datetime.strptime(texto_fecha[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def esta_vencida(tarea):
    """Una tarea esta vencida si paso su fecha y no esta terminada.

    Una tarea terminada fuera de plazo ya no es un problema que resolver, de
    modo que marcarla en rojo solo anadiria ruido a la pantalla.
    """
    if tarea.get("estado") == "Terminado":
        return False
    limite = _fecha_de(tarea)
    return bool(limite and limite < date.today())


def texto_de_plazo(tarea):
    """Describe el plazo en lenguaje natural: "vence manana", "hace 3 dias"."""
    limite = _fecha_de(tarea)
    if not limite:
        return "", Tono.TINTA_SUAVE
    dias = (limite - date.today()).days
    if tarea.get("estado") == "Terminado":
        return limite.strftime("%d/%m"), Tono.TINTA_SUAVE
    if dias < -1:
        return f"vencio hace {abs(dias)} dias", Tono.PELIGRO
    if dias == -1:
        return "vencio ayer", Tono.PELIGRO
    if dias == 0:
        return "vence hoy", Tono.PELIGRO
    if dias == 1:
        return "vence manana", Tono.AVISO
    if dias <= 3:
        return f"vence en {dias} dias", Tono.AVISO
    return limite.strftime("%d/%m"), Tono.TINTA_SUAVE


def fecha_valida(texto_fecha):
    """Una fecha vacia es valida; una escrita a mano tiene que ser real.

    El campo admite teclearla y tambien elegirla en el calendario. Admitir
    ambas cosas exige comprobar la escrita: sin esta validacion se podia
    guardar "manana" y el tablero no sabria si esa tarea esta vencida.
    """
    texto_fecha = (texto_fecha or "").strip()
    if not texto_fecha:
        return True
    try:
        datetime.strptime(texto_fecha, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def vibrar(estado, intenso=False):
    """Golpecito en el telefono al llegar un cambio ajeno.

    En un navegador de escritorio no hace nada. Es lo mas parecido a una
    notificacion que se puede dar sin salir de la aplicacion, y llega incluso
    cuando el usuario no esta mirando la pantalla.
    """
    try:
        if estado.vibracion is None:
            return
        if intenso:
            estado.vibracion.medium_impact()
        else:
            estado.vibracion.light_impact()
    except Exception:  # noqa: BLE001
        pass


def avisar(page, mensaje, color=None):
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(value=mensaje, color=ft.Colors.WHITE, size=13),
            bgcolor=color or Tono.TINTA,
            duration=3500,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.Margin.all(12),
            shape=ft.RoundedRectangleBorder(radius=10),
        )
    )


def ejecutar_fuera_del_hilo(page, estado, control, etiqueta_espera, trabajo,
                            al_terminar):
    """Ejecuta una escritura en otro hilo y muestra que se esta trabajando.

    `trabajo` corre fuera del hilo de la interfaz y no debe tocar controles.
    `al_terminar(resultado, error)` corre despues, y es el unico que modifica
    la pantalla.

    El detalle que hace que esto funcione: al_terminar NO se ejecuta en el
    hilo trabajador, sino que se devuelve al bucle de eventos con
    call_soon_threadsafe. Hace falta porque page.navigate() programa una tarea
    asincrona y, fuera del bucle, lanza "no running event loop". Sin esta
    devolucion, un registro correcto terminaba sin error visible y sin
    cambiar de pantalla: el fallo mas dificil de diagnosticar de todos, el que
    no deja rastro.
    """
    etiqueta_original = control.content
    icono_original = control.icon
    control.disabled = True
    control.content = etiqueta_espera
    control.icon = None
    page.update()

    def devolver(resultado, error):
        control.disabled = False
        control.content = etiqueta_original
        control.icon = icono_original
        try:
            al_terminar(resultado, error)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

    def envoltura():
        resultado, error = None, None
        try:
            resultado = trabajo()
        except Exception as fallo:  # noqa: BLE001
            error = fallo
        bucle = getattr(estado, "bucle", None)
        if bucle is None:
            devolver(resultado, error)
        else:
            bucle.call_soon_threadsafe(devolver, resultado, error)

    page.run_thread(envoltura)


def banda_de_conexion(estado):
    """Franja de estado de la conexion (REQ-10).

    Advertencia sobre su alcance, comprobada con un navegador real al que se
    le corto la red: esta franja la dibuja el servidor, de modo que solo puede
    aparecer si hay conexion para enviarla. Durante un corte real no se vera.
    Queda porque si cubre el caso en que el dispositivo informa que se quedo
    sin red mientras la conexion con el servidor aun no ha muerto —cambio a
    modo avion con la aplicacion abierta, tipicamente—, pero el mecanismo que
    de verdad sostiene el REQ-10 es la recuperacion: cuando la conexion vuelve,
    page.on_connect si llega al servidor y entonces se relee el tablero y se
    avisa. La deteccion durante el corte la hace el cliente de Flet por su
    cuenta, con su propio reintento y su propio aviso.
    """
    return ft.Container(
        visible=estado.sin_conexion,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        bgcolor=Tono.AVISO_FONDO,
        border_radius=ft.BorderRadius.all(10),
        content=ft.Row(
            spacing=9,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.CLOUD_OFF_OUTLINED, size=16, color=Tono.AVISO),
                ft.Text(
                    value="Sin conexion. Sus cambios no se estan compartiendo.",
                    size=12,
                    weight=ft.FontWeight.W_500,
                    color=Tono.AVISO,
                    expand=True,
                ),
            ],
        ),
    )


def centrar(estado, controles):
    """Columna de contenido centrada que se adapta al ancho de la pantalla."""
    estado.banda = banda_de_conexion(estado)
    return ft.ResponsiveRow(
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            ft.Container(
                col=COLUMNAS,
                padding=ft.Padding.symmetric(horizontal=18, vertical=18),
                content=ft.Column(
                    controls=[estado.banda] + controles,
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            )
        ],
    )


def tarjeta(contenido, relleno=16, borde=None, fondo=None):
    return ft.Container(
        padding=ft.Padding.all(relleno),
        bgcolor=fondo or Tono.SUPERFICIE,
        border=ft.Border.all(1, borde or Tono.BORDE),
        border_radius=ft.BorderRadius.all(14),
        content=contenido,
    )


def texto(valor, tamano=13, peso=ft.FontWeight.W_400, color=None, **extra):
    return ft.Text(
        value=valor, size=tamano, weight=peso, color=color or Tono.TINTA, **extra
    )


def rotulo(valor):
    return ft.Text(
        value=valor.upper(), size=10, weight=ft.FontWeight.W_600,
        color=Tono.TINTA_TENUE,
    )


def campo(etiqueta, **extra):
    return ft.TextField(
        label=etiqueta,
        border_color=Tono.BORDE,
        focused_border_color=Tono.ACENTO,
        border_radius=ft.BorderRadius.all(10),
        border_width=1,
        focused_border_width=2,
        text_size=14,
        label_style=ft.TextStyle(size=13, color=Tono.TINTA_SUAVE),
        content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
        cursor_color=Tono.ACENTO,
        **extra,
    )


def boton(etiqueta, al_pulsar, icono=None, principal=True, peligro=False):
    if peligro:
        fondo, tinta = Tono.PELIGRO, ft.Colors.WHITE
    elif principal:
        fondo, tinta = Tono.ACENTO, ft.Colors.WHITE
    else:
        fondo, tinta = Tono.BORDE_TENUE, Tono.TINTA
    return ft.Button(
        content=etiqueta,
        icon=icono,
        on_click=al_pulsar,
        bgcolor=fondo,
        color=tinta,
        elevation=0,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=18, vertical=18),
            text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_600),
        ),
    )


def boton_discreto(etiqueta, al_pulsar, icono=None, color=None):
    return ft.TextButton(
        content=etiqueta,
        icon=icono,
        on_click=al_pulsar,
        style=ft.ButtonStyle(
            color=color or Tono.TINTA_SUAVE,
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_500),
        ),
    )


def boton_estado(destino, al_pulsar):
    """Boton de cambio de estado con forma de boton, no de enlace.

    Antes eran texto de color y varias personas no se daban cuenta de que se
    podian pulsar. Un contorno y un fondo tenue bastan para que se lean como
    algo accionable sin competir con la accion principal de la pantalla.
    """
    tinta, fondo = ESTILO_ESTADO.get(destino, (Tono.TINTA_SUAVE, Tono.BORDE_TENUE))
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=13, vertical=9),
        bgcolor=fondo,
        border_radius=ft.BorderRadius.all(9),
        ink=True,
        on_click=al_pulsar,
        content=ft.Row(
            spacing=5,
            tight=True,
            controls=[
                ft.Icon(ft.Icons.ARROW_FORWARD, size=12, color=tinta),
                ft.Text(value=destino, size=12, weight=ft.FontWeight.W_600,
                        color=tinta),
            ],
        ),
    )


def boton_icono(icono, descripcion, al_pulsar, color=None):
    """Boton de icono con area tactil suficiente.

    Los iconos se dibujan a 18 px pero el area sensible llega a 40, que es el
    minimo con el que un dedo acierta de forma fiable. Antes eran 15 px sin
    relleno y habia que apuntar.
    """
    return ft.IconButton(
        icon=icono,
        icon_size=18,
        icon_color=color or Tono.TINTA_SUAVE,
        tooltip=descripcion,
        on_click=al_pulsar,
        padding=ft.Padding.all(11),
        size_constraints=ft.BoxConstraints(min_width=40, min_height=40),
    )


def circulo(nombre, medida=16):
    return ft.CircleAvatar(
        radius=medida,
        bgcolor=color_de_persona(nombre),
        content=ft.Text(value=iniciales(nombre), size=medida * 0.62,
                        weight=ft.FontWeight.W_700, color=ft.Colors.WHITE),
    )


def insignia(etiqueta, tinta, fondo, compacta=False):
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=9, vertical=3),
        bgcolor=fondo,
        border_radius=ft.BorderRadius.all(20),
        content=ft.Text(value=etiqueta, size=10 if compacta else 11,
                        weight=ft.FontWeight.W_600, color=tinta),
    )


def insignia_estado(nombre_estado, compacta=False):
    tinta, fondo = ESTILO_ESTADO.get(nombre_estado,
                                    (Tono.TINTA_SUAVE, Tono.BORDE_TENUE))
    return insignia(nombre_estado, tinta, fondo, compacta)


def vacio(mensaje, icono=ft.Icons.INBOX_OUTLINED, accion=None):
    """Estado vacio. Si hay una accion obvia, la ofrece en el mismo sitio."""
    contenido = [
        ft.Icon(icono, size=28, color=Tono.TINTA_TENUE),
        ft.Text(value=mensaje, size=13, color=Tono.TINTA_SUAVE,
                text_align=ft.TextAlign.CENTER),
    ]
    if accion is not None:
        contenido.append(ft.Container(height=4))
        contenido.append(accion)
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=20, vertical=36),
        alignment=ft.Alignment.CENTER,
        content=ft.Column(spacing=10,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=contenido),
    )


def marca_sync(medida=38):
    return ft.Container(
        width=medida,
        height=medida,
        bgcolor=Tono.ACENTO,
        border_radius=ft.BorderRadius.all(medida * 0.3),
        alignment=ft.Alignment.CENTER,
        content=ft.Text(value="ST", size=medida * 0.42,
                        weight=ft.FontWeight.W_700, color=ft.Colors.WHITE),
    )


def barra(page, estado, titulo_texto, con_volver=False, ruta_de_regreso=None,
          acciones=None):
    acciones = list(acciones or [])
    if estado.usuario:
        acciones.append(
            boton_icono(ft.Icons.LOGOUT, "Cerrar sesion",
                        lambda e: cerrar_sesion(page, estado))
        )
    principal = None
    if con_volver:
        principal = boton_icono(
            ft.Icons.ARROW_BACK, "Volver",
            lambda e: page.navigate(ruta_de_regreso or "/proyectos"),
            color=Tono.TINTA,
        )
    return ft.AppBar(
        leading=principal,
        automatically_imply_leading=False,
        title=ft.Text(value=titulo_texto, size=16, weight=ft.FontWeight.W_600,
                      color=Tono.TINTA, max_lines=1,
                      overflow=ft.TextOverflow.ELLIPSIS),
        center_title=False,
        bgcolor=Tono.SUPERFICIE,
        elevation=0,
        toolbar_height=58,
        actions=acciones,
    )


def cerrar_sesion(page, estado):
    """Cierra la sesion, revoca el testigo y suelta la suscripcion (REQ-11).

    Revocar el testigo es la parte que no se debe olvidar: sin eso, "cerrar
    sesion" solo borraria el nombre de la pantalla y el dispositivo seguiria
    teniendo una llave valida.
    """
    soltar_suscripcion(page, estado)
    try:
        recordar.olvidar(estado.preferencias)
    except Exception:  # noqa: BLE001
        pass
    estado.usuario = None
    estado.id_proyecto = None
    estado.proyecto = None
    page.navigate("/")


def soltar_suscripcion(page, estado):
    if estado.suscrito_a:
        try:
            tareas.cancelar_suscripcion(page, estado.suscrito_a)
        except Exception:  # noqa: BLE001
            pass
        estado.suscrito_a = None
    estado.refrescar = None


# ===========================================================================
# Pantalla de ingreso
# ===========================================================================


def vista_ingreso(page, estado):
    correo = campo("Correo institucional", keyboard_type=ft.KeyboardType.EMAIL,
                   autofocus=True)
    clave = campo("Contrasena", password=True, can_reveal_password=True)
    error = ft.Text(value="", color=Tono.PELIGRO, size=12, visible=False)

    def mostrar_error(mensaje):
        error.value = mensaje
        error.visible = bool(mensaje)
        page.update()

    def entrar(e):
        mostrar_error("")
        if not correo.value or not clave.value:
            mostrar_error("Escriba su correo y su contrasena.")
            return

        datos = (correo.value.strip(), clave.value)

        def trabajo():
            usuario = sesion.autenticar(*datos)
            if usuario:
                recordar.recordar(estado.preferencias, usuario["id_usuario"])
            return usuario

        def al_terminar(usuario, fallo):
            if fallo:
                mostrar_error(f"No se pudo verificar la cuenta: {fallo}")
                return
            if not usuario:
                # Un mensaje unico para correo inexistente y contrasena
                # incorrecta: distinguirlos permitiria averiguar que correos
                # estan registrados.
                mostrar_error("El correo o la contrasena no coinciden.")
                return
            estado.usuario = usuario
            page.navigate("/proyectos")

        ejecutar_fuera_del_hilo(page, estado, boton_entrar, "Verificando...", trabajo,
                                al_terminar)

    boton_entrar = boton("Ingresar", entrar, ft.Icons.ARROW_FORWARD)
    clave.on_submit = entrar

    return ft.View(
        route="/",
        bgcolor=Tono.FONDO,
        controls=[
            centrar(
                estado,
                [
                    ft.Container(height=26),
                    ft.Row(alignment=ft.MainAxisAlignment.CENTER,
                           controls=[marca_sync(46)]),
                    ft.Container(height=2),
                    ft.Text(value="SyncTask CUN", size=26,
                            weight=ft.FontWeight.W_700, color=Tono.TINTA,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text(
                        value="Las tareas del equipo, iguales para todos y al instante",
                        size=13, color=Tono.TINTA_SUAVE,
                        text_align=ft.TextAlign.CENTER),
                    ft.Container(height=10),
                    tarjeta(
                        ft.Column(
                            spacing=14,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[correo, clave, error, boton_entrar],
                        ),
                        relleno=18,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=4,
                        controls=[
                            ft.Text(value="No tiene cuenta?", size=13,
                                    color=Tono.TINTA_SUAVE),
                            boton_discreto("Registrarse",
                                           lambda e: page.navigate("/registro"),
                                           color=Tono.ACENTO),
                        ],
                    ),
                ],
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ===========================================================================
# Pantalla de registro
# ===========================================================================


def vista_registro(page, estado):
    nombre = campo("Nombre y apellido", autofocus=True)
    correo = campo("Correo institucional", keyboard_type=ft.KeyboardType.EMAIL)
    clave = campo("Contrasena", password=True, can_reveal_password=True)
    repetir = campo("Repita la contrasena", password=True,
                    can_reveal_password=True)
    autoriza = ft.Checkbox(
        label="Autorizo el tratamiento de mis datos personales",
        value=False, active_color=Tono.ACENTO,
        label_style=ft.TextStyle(size=13, color=Tono.TINTA),
    )
    error = ft.Text(value="", color=Tono.PELIGRO, size=12, visible=False)

    aviso = ft.Container(
        padding=ft.Padding.all(14),
        bgcolor=Tono.ACENTO_FONDO,
        border_radius=ft.BorderRadius.all(12),
        content=ft.Column(
            spacing=7,
            controls=[
                ft.Row(spacing=7, controls=[
                    ft.Icon(ft.Icons.SHIELD_OUTLINED, size=15, color=Tono.ACENTO),
                    ft.Text(value="Aviso de privacidad", size=12,
                            weight=ft.FontWeight.W_700, color=Tono.ACENTO),
                ]),
                ft.Text(value=AVISO_DE_PRIVACIDAD, size=11, color="#4338CA",
                        selectable=True),
            ],
        ),
    )

    def mostrar_error(mensaje):
        error.value = mensaje
        error.visible = bool(mensaje)
        page.update()

    def registrar(e):
        mostrar_error("")
        if not nombre.value or not correo.value or not clave.value:
            mostrar_error("Complete el nombre, el correo y la contrasena.")
            return
        if clave.value != repetir.value:
            mostrar_error("Las dos contrasenas no coinciden.")
            return
        if len(clave.value) < 8:
            mostrar_error("La contrasena debe tener al menos ocho caracteres.")
            return
        if not autoriza.value:
            # REQ-08: sin autorizacion expresa no se crea la cuenta. La
            # comprobacion se repite en sesion.registrar_usuario, que es la que
            # cuenta; esta solo evita un viaje a la base de datos.
            mostrar_error("Debe autorizar el tratamiento de datos para continuar.")
            return

        datos = (nombre.value.strip(), correo.value.strip(), clave.value)

        def trabajo():
            usuario = sesion.registrar_usuario(datos[0], datos[1], datos[2], True)
            recordar.recordar(estado.preferencias, usuario["id_usuario"])
            return usuario

        def al_terminar(usuario, fallo):
            if fallo:
                mostrar_error(str(fallo))
                return
            estado.usuario = usuario
            avisar(page, "Cuenta creada. Bienvenido.", Tono.EXITO)
            page.navigate("/proyectos")

        ejecutar_fuera_del_hilo(page, estado, boton_crear, "Creando...", trabajo,
                                al_terminar)

    boton_crear = boton("Crear mi cuenta", registrar, ft.Icons.PERSON_ADD_ALT)

    return ft.View(
        route="/registro",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, "Crear cuenta", con_volver=True,
                     ruta_de_regreso="/"),
        controls=[
            centrar(estado, [
                tarjeta(
                    ft.Column(spacing=14,
                              horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                              controls=[nombre, correo, clave, repetir]),
                    relleno=18,
                ),
                aviso,
                autoriza,
                error,
                boton_crear,
            ])
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ===========================================================================
# Pantalla de proyectos
# ===========================================================================


def vista_proyectos(page, estado):
    codigo = campo("Codigo de acceso", hint_text="Seis caracteres",
                   capitalization=ft.TextCapitalization.CHARACTERS)
    nombre = campo("Nombre del proyecto")
    descripcion = campo("Descripcion (opcional)", multiline=True, min_lines=2)
    lista = ft.Column(spacing=10)

    def pintar_lista():
        lista.controls.clear()
        try:
            mios = proyectos.listar_mios(estado.usuario)
        except Exception as fallo:  # noqa: BLE001
            lista.controls.append(
                texto(f"No se pudieron cargar los proyectos: {fallo}", 12,
                      color=Tono.PELIGRO))
            return
        if not mios:
            lista.controls.append(
                vacio("Todavia no pertenece a ningun proyecto.",
                      ft.Icons.FOLDER_OPEN_OUTLINED))
            return
        for elemento in mios:
            id_proyecto = elemento.get("id_proyecto")
            nombre_proyecto = elemento.get("nombre_proyecto", "Proyecto")
            es_admin = elemento.get("rol") == "administrador"
            lista.controls.append(
                ft.Container(
                    padding=ft.Padding.all(14),
                    bgcolor=Tono.SUPERFICIE,
                    border=ft.Border.all(1, Tono.BORDE),
                    border_radius=ft.BorderRadius.all(14),
                    ink=True,
                    on_click=lambda e, i=id_proyecto: page.navigate(f"/tablero/{i}"),
                    content=ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(
                                width=38, height=38,
                                bgcolor=color_de_persona(nombre_proyecto),
                                border_radius=ft.BorderRadius.all(11),
                                alignment=ft.Alignment.CENTER,
                                content=ft.Text(
                                    value=iniciales(nombre_proyecto), size=14,
                                    weight=ft.FontWeight.W_700,
                                    color=ft.Colors.WHITE),
                            ),
                            ft.Column(
                                spacing=2, expand=True,
                                controls=[
                                    texto(nombre_proyecto, 14,
                                          ft.FontWeight.W_600, max_lines=1,
                                          overflow=ft.TextOverflow.ELLIPSIS),
                                    texto("Administrador" if es_admin
                                          else "Integrante", 11,
                                          color=Tono.TINTA_TENUE),
                                ],
                            ),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20,
                                    color=Tono.TINTA_TENUE),
                        ],
                    ),
                )
            )

    def unirse(e):
        valor = codigo.value

        def trabajo():
            return proyectos.unirse(valor, estado.usuario)

        def al_terminar(proyecto, fallo):
            if isinstance(fallo, proyectos.ErrorDeCodigo):
                avisar(page, str(fallo), Tono.PELIGRO)
                return
            if fallo:
                avisar(page, f"No se pudo vincular: {fallo}", Tono.PELIGRO)
                return
            codigo.value = ""
            avisar(page, f"Se vinculo a {proyecto.get('nombre', 'el proyecto')}.",
                   Tono.EXITO)
            page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

        ejecutar_fuera_del_hilo(page, estado, boton_unirme, "Buscando...", trabajo,
                                al_terminar)

    def crear(e):
        datos = (nombre.value, descripcion.value)

        def trabajo():
            return proyectos.crear(datos[0], datos[1], estado.usuario)

        def al_terminar(proyecto, fallo):
            if isinstance(fallo, ValueError):
                avisar(page, str(fallo), Tono.PELIGRO)
                return
            if fallo:
                avisar(page, f"No se pudo crear el proyecto: {fallo}",
                       Tono.PELIGRO)
                return
            nombre.value = ""
            descripcion.value = ""
            avisar(page, f"Proyecto creado. Codigo: {proyecto.get('codigo_acceso','')}",
                   Tono.EXITO)
            page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

        ejecutar_fuera_del_hilo(page, estado, boton_crear, "Creando...", trabajo,
                                al_terminar)

    boton_unirme = boton("Unirme", unirse, ft.Icons.GROUP_ADD_OUTLINED,
                         principal=False)
    boton_crear = boton("Crear proyecto", crear, ft.Icons.ADD)

    pintar_lista()

    return ft.View(
        route="/proyectos",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado,
                     f"Hola, {estado.usuario['nombre'].split()[0]}"),
        controls=[
            centrar(estado, [
                rotulo("Mis proyectos"),
                lista,
                ft.Container(height=6),
                rotulo("Unirme con un codigo"),
                tarjeta(ft.Column(
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[codigo, boton_unirme])),
                ft.Container(height=6),
                rotulo("Crear un proyecto nuevo"),
                tarjeta(ft.Column(
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[nombre, descripcion, boton_crear])),
            ])
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ===========================================================================
# Pantalla de tablero
# ===========================================================================


def vista_tablero(page, estado, id_proyecto):
    """Tablero del proyecto con actualizacion en vivo (REQ-03, 04, 05, 09)."""
    estado.id_proyecto = id_proyecto
    usuario = estado.usuario

    try:
        estado.proyecto = proyectos.obtener(id_proyecto, usuario)
    except tareas.ErrorDePermiso:
        avisar(page, "No pertenece a ese proyecto.", Tono.PELIGRO)
        return None
    if not estado.proyecto:
        avisar(page, "El proyecto ya no existe.", Tono.PELIGRO)
        return None

    nombre_proyecto = estado.proyecto.get("nombre", "Proyecto")
    codigo_acceso = estado.proyecto.get("codigo_acceso", "")

    lista = ft.Column(spacing=10)
    filtros = ft.Row(spacing=7, wrap=True, run_spacing=7)
    formulario = ft.Container(visible=False)
    panel_invitar = ft.Container(visible=False)

    titulo_tarea = campo("Titulo de la tarea")
    descripcion_tarea = campo("Descripcion (opcional)", multiline=True,
                              min_lines=2)
    fecha_tarea = campo("Fecha limite", hint_text="AAAA-MM-DD o use el calendario")
    responsable_tarea = ft.Dropdown(
        label="Responsable", options=[],
        border_color=Tono.BORDE, focused_border_color=Tono.ACENTO,
        border_radius=ft.BorderRadius.all(10), text_size=14,
        label_style=ft.TextStyle(size=13, color=Tono.TINTA_SUAVE),
        content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
    )

    datos = {"tareas": []}
    conteo = {}

    # --- selector de fecha
    #
    # La fecha era texto libre y nadie validaba nada: se podia guardar "manana"
    # y el tablero no sabria que hacer con eso. Con el selector, el formato es
    # correcto por construccion y ademas se escribe mas rapido en un telefono.

    def fecha_elegida(e):
        valor = e.control.value
        fecha_tarea.value = valor.strftime("%Y-%m-%d") if valor else ""
        page.update()

    def abrir_calendario(e):
        actual = None
        if fecha_tarea.value:
            try:
                actual = datetime.strptime(fecha_tarea.value, "%Y-%m-%d")
            except ValueError:
                actual = None
        page.show_dialog(
            ft.DatePicker(
                value=actual,
                first_date=datetime(2024, 1, 1),
                last_date=datetime(2030, 12, 31),
                help_text="Fecha limite de la tarea",
                cancel_text="Cancelar",
                confirm_text="Elegir",
                on_change=fecha_elegida,
            )
        )

    def limpiar_fecha(e):
        fecha_tarea.value = ""
        page.update()

    # El calendario va en un boton propio al lado del campo y no dentro de el.
    # Como sufijo del campo no se dibujaba: un control compuesto ahi dentro no
    # recibe un ancho util y acaba midiendo cero.
    fila_fecha = ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(content=fecha_tarea, expand=True),
            boton_icono(ft.Icons.CALENDAR_MONTH_OUTLINED, "Elegir en el calendario",
                        abrir_calendario, color=Tono.ACENTO),
            boton_icono(ft.Icons.BACKSPACE_OUTLINED, "Quitar la fecha",
                        limpiar_fecha),
        ],
    )

    def integrantes():
        try:
            from datos import repositorio

            return repositorio.listar_integrantes(id_proyecto)
        except Exception:  # noqa: BLE001
            return []

    def cargar_responsables():
        responsable_tarea.options = [
            ft.DropdownOption(key=n, text=n)
            for n in sorted({i.get("nombre_usuario") or i.get("nombre")
                             or "Sin nombre" for i in integrantes()})
        ]
        if not responsable_tarea.value and responsable_tarea.options:
            responsable_tarea.value = responsable_tarea.options[0].key

    # --- tarjeta de tarea

    def tarjeta_de_tarea(tarea):
        id_tarea = tarea["SK"].split("#", 1)[1]
        estado_actual = tarea.get("estado", "Pendiente")
        n_comentarios = conteo.get(id_tarea, 0)
        responsable = tarea.get("responsable", "")
        resumen = (tarea.get("descripcion") or "").strip()
        plazo, color_plazo = texto_de_plazo(tarea)
        vencida = esta_vencida(tarea)

        encabezado = [
            texto(tarea.get("titulo", "(sin titulo)"), 14.5,
                  ft.FontWeight.W_600, expand=True, max_lines=3,
                  overflow=ft.TextOverflow.ELLIPSIS),
            insignia_estado(estado_actual, compacta=True),
        ]

        cuerpo = [ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                         vertical_alignment=ft.CrossAxisAlignment.START,
                         spacing=10, controls=encabezado)]

        # Una linea de la descripcion: antes la tarjeta solo mostraba el
        # titulo y habia que abrir la tarea para saber de que iba.
        if resumen:
            cuerpo.append(
                texto(resumen, 12, color=Tono.TINTA_SUAVE, max_lines=2,
                      overflow=ft.TextOverflow.ELLIPSIS))

        meta = [
            circulo(responsable, 10) if responsable else ft.Container(),
            texto(responsable or "Sin responsable", 11.5,
                  color=Tono.TINTA_SUAVE),
        ]
        if plazo:
            meta.append(ft.Container(width=3, height=3, bgcolor=Tono.TINTA_TENUE,
                                     border_radius=ft.BorderRadius.all(2)))
            meta.append(
                ft.Row(spacing=4, tight=True, controls=[
                    ft.Icon(ft.Icons.SCHEDULE, size=12, color=color_plazo)
                    if vencida or color_plazo == Tono.AVISO else ft.Container(),
                    ft.Text(value=plazo, size=11.5, color=color_plazo,
                            weight=ft.FontWeight.W_600 if vencida
                            else ft.FontWeight.W_400),
                ]))
        cuerpo.append(ft.Row(spacing=8, wrap=True, run_spacing=4,
                             vertical_alignment=ft.CrossAxisAlignment.CENTER,
                             controls=meta))

        cuerpo.append(ft.Divider(height=1, thickness=1, color=Tono.BORDE_TENUE))
        cuerpo.append(
            ft.Row(
                wrap=True, spacing=6, run_spacing=6,
                controls=[
                    boton_estado(d, lambda e, i=id_tarea, x=d: mover(i, x))
                    for d in ESTADOS if d != estado_actual
                ] + [
                    boton_discreto(
                        f"Comentarios ({n_comentarios})" if n_comentarios
                        else "Comentar",
                        lambda e, i=id_tarea: page.navigate(
                            f"/tarea/{id_proyecto}/{i}"),
                        ft.Icons.CHAT_BUBBLE_OUTLINE,
                        color=Tono.ACENTO if n_comentarios else Tono.TINTA_SUAVE),
                ],
            )
        )

        return ft.Container(
            padding=ft.Padding.only(left=16, top=14, right=12, bottom=10),
            bgcolor=Tono.SUPERFICIE,
            border=ft.Border.all(1, Tono.PELIGRO if vencida else Tono.BORDE),
            border_radius=ft.BorderRadius.all(14),
            content=ft.Column(spacing=9, controls=cuerpo),
        )

    # --- datos y pintado

    def recargar():
        datos["tareas"] = tareas.vigentes(
            tareas.listar(id_proyecto, usuario["id_usuario"]))
        conteo.clear()
        try:
            conteo.update(comentarios.contar_por_tarea(id_proyecto,
                                                       usuario["id_usuario"]))
        except Exception:  # noqa: BLE001
            pass

    def pasa_el_filtro(tarea):
        if estado.filtro == FILTRO_TODAS:
            return True
        if estado.filtro == FILTRO_MIAS:
            return tarea.get("responsable") == usuario["nombre"]
        if estado.filtro == FILTRO_VENCIDAS:
            return esta_vencida(tarea)
        return tarea.get("estado") == estado.filtro

    def construir(actualizar):
        por_estado = {e: 0 for e in ESTADOS}
        for t in datos["tareas"]:
            clave = t.get("estado", "Pendiente")
            por_estado[clave] = por_estado.get(clave, 0) + 1
        n_mias = sum(1 for t in datos["tareas"]
                     if t.get("responsable") == usuario["nombre"])
        n_vencidas = sum(1 for t in datos["tareas"] if esta_vencida(t))

        etiquetas = [(FILTRO_TODAS, len(datos["tareas"])),
                     (FILTRO_MIAS, n_mias)]
        # El filtro de vencidas solo aparece cuando hay alguna: un filtro que
        # siempre marca cero es ruido, y cuando aparece llama la atencion justo
        # en el momento en que importa.
        if n_vencidas:
            etiquetas.append((FILTRO_VENCIDAS, n_vencidas))
        etiquetas += [(e, por_estado.get(e, 0)) for e in ESTADOS]

        filtros.controls.clear()
        for etiqueta, cantidad in etiquetas:
            activo = estado.filtro == etiqueta
            if etiqueta == FILTRO_VENCIDAS:
                color_propio = Tono.PELIGRO
            elif etiqueta in ESTILO_ESTADO:
                color_propio = ESTILO_ESTADO[etiqueta][0]
            else:
                color_propio = Tono.TINTA_SUAVE
            filtros.controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=13, vertical=9),
                    border_radius=ft.BorderRadius.all(20),
                    bgcolor=Tono.TINTA if activo else Tono.SUPERFICIE,
                    border=ft.Border.all(1, Tono.TINTA if activo else Tono.BORDE),
                    ink=True,
                    on_click=lambda e, f=etiqueta: filtrar(f),
                    content=ft.Row(spacing=6, tight=True, controls=[
                        ft.Text(value=etiqueta, size=12,
                                weight=ft.FontWeight.W_600,
                                color=ft.Colors.WHITE if activo else color_propio),
                        ft.Text(value=str(cantidad), size=12,
                                color=ft.Colors.WHITE if activo
                                else Tono.TINTA_TENUE),
                    ]),
                )
            )

        visibles = [t for t in datos["tareas"] if pasa_el_filtro(t)]
        orden = {e: n for n, e in enumerate(ESTADOS)}
        # Las vencidas primero: es la unica reordenacion que responde a una
        # urgencia real y no a una preferencia de presentacion.
        visibles.sort(key=lambda t: (not esta_vencida(t),
                                     orden.get(t.get("estado", "Pendiente"), 9),
                                     t.get("fecha_limite") or "9999"))

        lista.controls.clear()
        if not visibles:
            if estado.filtro == FILTRO_TODAS:
                lista.controls.append(
                    vacio("Todavia no hay tareas en este proyecto.",
                          ft.Icons.TASK_ALT,
                          boton("Crear la primera", alternar_formulario,
                                ft.Icons.ADD)))
            else:
                lista.controls.append(
                    vacio(f"No hay tareas en «{estado.filtro.lower()}».",
                          ft.Icons.FILTER_ALT_OUTLINED,
                          boton_discreto("Ver todas",
                                         lambda e: filtrar(FILTRO_TODAS))))
        for t in visibles:
            lista.controls.append(tarjeta_de_tarea(t))

        if actualizar:
            page.update()

    def pintar():
        construir(actualizar=True)

    def filtrar(etiqueta):
        estado.filtro = etiqueta
        pintar()

    def refrescar_desde_evento(propio=False):
        """Lo invoca el manejador de PubSub, en otro hilo.

        `propio` lo ponen las acciones de esta misma sesion: a nadie hay que
        avisarle de lo que acaba de hacer. Sin esa distincion, cada vez que
        uno crea una tarea se avisa a si mismo, que es el defecto mas comun
        de este tipo de avisos.
        """
        try:
            # Foto del tablero antes de releer, para poder contar que cambio.
            antes = list(datos["tareas"])
            conteo_antes = dict(conteo)

            recargar()
            pintar()
            # La medicion se toma aqui y no antes: el requisito habla de
            # cuando el companero *ve* el cambio, de modo que el cronometro
            # para cuando la pantalla ya esta reconstruida (REQ-12).
            medicion.marcar_recepcion(id_proyecto, usuario["id_usuario"])

            if propio:
                return
            frases = avisos.describir(antes, datos["tareas"], conteo_antes,
                                      conteo)
            if not frases:
                return
            avisar(page, avisos.resumir(frases), Tono.ACENTO)
            vibrar(estado)
            titulo_aviso, cuerpo = avisos.titulo_y_cuerpo(frases,
                                                          nombre_proyecto)
            notificaciones.mostrar(estado.bucle, estado.notificador,
                                   titulo_aviso, cuerpo)
        except Exception:  # noqa: BLE001
            # Una sesion que ya navego a otra pantalla no debe tumbar el
            # proceso por intentar actualizar controles ausentes.
            pass

    # --- acciones

    def mover(id_tarea, destino):
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"],
                                "cambio_de_estado")
        try:
            tareas.cambiar_estado(page, id_proyecto, usuario["id_usuario"],
                                  id_tarea, destino)
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:  # noqa: BLE001
            avisar(page, f"No se pudo mover la tarea: {fallo}", Tono.PELIGRO)
            return
        refrescar_desde_evento(propio=True)

    def alternar_formulario(e):
        formulario.visible = not formulario.visible
        panel_invitar.visible = False
        if formulario.visible:
            cargar_responsables()
        page.update()

    def alternar_invitar(e):
        panel_invitar.visible = not panel_invitar.visible
        formulario.visible = False
        page.update()

    def copiar_codigo(e):
        """Copia el codigo al portapapeles del dispositivo.

        Antes el codigo era solo texto seleccionable, lo que en un telefono
        obliga a mantener pulsado, ajustar la seleccion y copiar. Un toque
        basta.
        """
        try:
            estado.portapapeles.set(codigo_acceso)
            avisar(page, "Codigo copiado.", Tono.EXITO)
        except Exception:  # noqa: BLE001
            avisar(page, "No se pudo copiar; seleccione el codigo a mano.",
                   Tono.PELIGRO)

    def guardar_tarea(e):
        if not titulo_tarea.value or not titulo_tarea.value.strip():
            avisar(page, "La tarea necesita un titulo.", Tono.PELIGRO)
            return
        if not fecha_valida(fecha_tarea.value):
            avisar(page, "La fecha debe tener la forma AAAA-MM-DD.", Tono.PELIGRO)
            return
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"],
                                "tarea_creada")
        campos = (titulo_tarea.value.strip(),
                  (descripcion_tarea.value or "").strip(),
                  responsable_tarea.value or usuario["nombre"],
                  (fecha_tarea.value or "").strip())

        def trabajo():
            return tareas.crear(page, id_proyecto, usuario["id_usuario"],
                                titulo=campos[0], descripcion=campos[1],
                                responsable=campos[2], fecha_limite=campos[3])

        def al_terminar(_, fallo):
            if fallo:
                avisar(page, f"No se pudo crear la tarea: {fallo}", Tono.PELIGRO)
                return
            titulo_tarea.value = ""
            descripcion_tarea.value = ""
            fecha_tarea.value = ""
            formulario.visible = False
            avisar(page, "Tarea creada.", Tono.EXITO)
            refrescar_desde_evento(propio=True)

        ejecutar_fuera_del_hilo(page, estado, boton_guardar, "Guardando...", trabajo,
                                al_terminar)

    boton_guardar = boton("Guardar", guardar_tarea, ft.Icons.CHECK)

    formulario.padding = ft.Padding.all(16)
    formulario.bgcolor = Tono.SUPERFICIE
    formulario.border = ft.Border.all(1, Tono.ACENTO)
    formulario.border_radius = ft.BorderRadius.all(14)
    formulario.content = ft.Column(
        spacing=12,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[
            rotulo("Nueva tarea"),
            titulo_tarea,
            responsable_tarea,
            fila_fecha,
            descripcion_tarea,
            ft.Row(alignment=ft.MainAxisAlignment.END, spacing=6, controls=[
                boton_discreto("Cancelar", alternar_formulario),
                boton_guardar,
            ]),
        ],
    )

    panel_invitar.padding = ft.Padding.all(16)
    panel_invitar.bgcolor = Tono.ACENTO_FONDO
    panel_invitar.border = ft.Border.all(1, Tono.ACENTO_BORDE)
    panel_invitar.border_radius = ft.BorderRadius.all(14)
    panel_invitar.content = ft.Column(
        spacing=10,
        controls=[
            rotulo("Codigo para invitar"),
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
                run_spacing=10,
                controls=[
                    ft.Text(value=codigo_acceso, size=28,
                            weight=ft.FontWeight.W_700, color=Tono.ACENTO,
                            selectable=True),
                    boton("Copiar", copiar_codigo, ft.Icons.COPY_OUTLINED,
                          principal=False),
                ],
            ),
            texto("Quien tenga este codigo podra unirse al proyecto y ver "
                  "todas sus tareas.", 11.5, color=Tono.TINTA_SUAVE),
        ],
    )

    # Suscripcion al tema del proyecto. Se abre aqui y la cierra el enrutador
    # al salir de la pantalla (REQ-05).
    soltar_suscripcion(page, estado)
    tareas.suscribir(page, id_proyecto, lambda mensaje: refrescar_desde_evento())
    estado.suscrito_a = id_proyecto
    estado.refrescar = refrescar_desde_evento

    try:
        recargar()
    except Exception as fallo:  # noqa: BLE001
        avisar(page, f"No se pudieron cargar las tareas: {fallo}", Tono.PELIGRO)

    vista = ft.View(
        route=f"/tablero/{id_proyecto}",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, nombre_proyecto, con_volver=True,
                     ruta_de_regreso="/proyectos",
                     acciones=[boton_icono(ft.Icons.PERSON_ADD_ALT_1_OUTLINED,
                                           "Invitar al proyecto",
                                           alternar_invitar)]),
        controls=[
            centrar(estado, [
                boton("Nueva tarea", alternar_formulario, ft.Icons.ADD),
                panel_invitar,
                formulario,
                filtros,
                lista,
            ])
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )
    construir(actualizar=False)
    return vista


# ===========================================================================
# Pantalla de detalle de tarea
# ===========================================================================


def vista_tarea(page, estado, id_proyecto, id_tarea):
    """Detalle de una tarea, con edicion, supresion y conversacion (REQ-06).

    La edicion de la tarea y la de los comentarios se resuelven dentro de la
    propia pantalla, sin dialogos: en un telefono un dialogo tapa aquello
    sobre lo que se esta decidiendo.
    """
    usuario = estado.usuario
    try:
        proyectos.obtener(id_proyecto, usuario)
    except tareas.ErrorDePermiso:
        avisar(page, "No pertenece a ese proyecto.", Tono.PELIGRO)
        return None

    estado_vista = {"tarea": None, "editando": False, "borrando": False}

    def cargar_tarea():
        try:
            for t in tareas.vigentes(tareas.listar(id_proyecto,
                                                   usuario["id_usuario"])):
                if t["SK"].split("#", 1)[1] == id_tarea:
                    return t
        except Exception as fallo:  # noqa: BLE001
            avisar(page, f"No se pudo cargar la tarea: {fallo}", Tono.PELIGRO)
        return None

    estado_vista["tarea"] = cargar_tarea()
    if estado_vista["tarea"] is None:
        avisar(page, "Esa tarea ya no existe.", Tono.PELIGRO)
        return None

    ficha = ft.Container()
    hilo = ft.Column(spacing=10)
    nuevo = campo("Escriba un comentario", multiline=True, min_lines=2,
                  max_lines=6)

    edicion = {"clave": None, "borrador": ""}
    confirmando = {"clave": None}

    # --- ficha de la tarea, en sus tres estados

    def ficha_lectura():
        tarea = estado_vista["tarea"]
        responsable = tarea.get("responsable", "")
        plazo, color_plazo = texto_de_plazo(tarea)
        vencida = esta_vencida(tarea)

        meta = [
            circulo(responsable, 10) if responsable else ft.Container(),
            texto(responsable or "Sin responsable", 12, color=Tono.TINTA_SUAVE),
        ]
        if plazo:
            meta.append(ft.Text(value=plazo, size=12, color=color_plazo,
                                weight=ft.FontWeight.W_600 if vencida
                                else ft.FontWeight.W_400))

        return tarjeta(
            ft.Column(spacing=10, controls=[
                ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.START,
                       spacing=10,
                       controls=[
                           texto(tarea.get("titulo", "Tarea"), 17,
                                 ft.FontWeight.W_700, expand=True),
                           insignia_estado(tarea.get("estado", "Pendiente")),
                       ]),
                ft.Row(spacing=8, wrap=True, run_spacing=4,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=meta),
                ft.Divider(height=1, thickness=1, color=Tono.BORDE_TENUE),
                texto(tarea.get("descripcion", "") or "Sin descripcion.", 13,
                      color=Tono.TINTA_SUAVE, selectable=True),
            ]),
            borde=Tono.PELIGRO if vencida else None,
        )

    def ficha_edicion():
        tarea = estado_vista["tarea"]
        titulo_e = campo("Titulo de la tarea", value=tarea.get("titulo", ""),
                         autofocus=True)
        descripcion_e = campo("Descripcion", value=tarea.get("descripcion", ""),
                              multiline=True, min_lines=2)
        fecha_e = campo("Fecha limite", value=tarea.get("fecha_limite", ""),
                        hint_text="AAAA-MM-DD o use el calendario")

        try:
            from datos import repositorio

            nombres = sorted({i.get("nombre_usuario") or i.get("nombre")
                              or "Sin nombre"
                              for i in repositorio.listar_integrantes(id_proyecto)})
        except Exception:  # noqa: BLE001
            nombres = []
        if tarea.get("responsable") and tarea["responsable"] not in nombres:
            nombres.append(tarea["responsable"])
        responsable_e = ft.Dropdown(
            label="Responsable",
            value=tarea.get("responsable") or (nombres[0] if nombres else None),
            options=[ft.DropdownOption(key=n, text=n) for n in nombres],
            border_color=Tono.BORDE, focused_border_color=Tono.ACENTO,
            border_radius=ft.BorderRadius.all(10), text_size=14,
            label_style=ft.TextStyle(size=13, color=Tono.TINTA_SUAVE),
            content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
        )

        def fecha_elegida(e):
            valor = e.control.value
            fecha_e.value = valor.strftime("%Y-%m-%d") if valor else ""
            page.update()

        def abrir_calendario(e):
            actual = None
            if fecha_e.value:
                try:
                    actual = datetime.strptime(fecha_e.value, "%Y-%m-%d")
                except ValueError:
                    actual = None
            page.show_dialog(ft.DatePicker(
                value=actual, first_date=datetime(2024, 1, 1),
                last_date=datetime(2030, 12, 31),
                help_text="Fecha limite de la tarea", cancel_text="Cancelar",
                confirm_text="Elegir", on_change=fecha_elegida))

        fila_fecha_e = ft.Row(
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(content=fecha_e, expand=True),
                boton_icono(ft.Icons.CALENDAR_MONTH_OUTLINED,
                            "Elegir en el calendario", abrir_calendario,
                            color=Tono.ACENTO),
                boton_icono(ft.Icons.BACKSPACE_OUTLINED, "Quitar la fecha",
                            lambda e: (setattr(fecha_e, "value", ""),
                                       page.update())),
            ])

        def guardar(e):
            if not titulo_e.value or not titulo_e.value.strip():
                avisar(page, "La tarea necesita un titulo.", Tono.PELIGRO)
                return
            if not fecha_valida(fecha_e.value):
                avisar(page, "La fecha debe tener la forma AAAA-MM-DD.",
                       Tono.PELIGRO)
                return
            medicion.marcar_emision(id_proyecto, usuario["id_usuario"],
                                    "tarea_editada")
            campos = (titulo_e.value, descripcion_e.value,
                      responsable_e.value, fecha_e.value)

            def trabajo():
                return tareas.editar(page, id_proyecto, usuario["id_usuario"],
                                     id_tarea, titulo=campos[0],
                                     descripcion=campos[1],
                                     responsable=campos[2],
                                     fecha_limite=campos[3])

            def al_terminar(tarea_nueva, fallo):
                if fallo:
                    avisar(page, f"No se pudo guardar: {fallo}", Tono.PELIGRO)
                    return
                estado_vista["tarea"] = tarea_nueva
                estado_vista["editando"] = False
                avisar(page, "Tarea actualizada.", Tono.EXITO)
                repintar()

            ejecutar_fuera_del_hilo(page, estado, boton_guardar_tarea, "Guardando...",
                                    trabajo, al_terminar)

        boton_guardar_tarea = boton("Guardar cambios", guardar, ft.Icons.CHECK)

        return tarjeta(
            ft.Column(spacing=12,
                      horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                      controls=[
                          rotulo("Editar tarea"),
                          titulo_e, responsable_e, fila_fecha_e,
                          descripcion_e,
                          ft.Row(alignment=ft.MainAxisAlignment.END, spacing=6,
                                 controls=[
                                     boton_discreto("Cancelar",
                                                    lambda e: cancelar_edicion_tarea()),
                                     boton_guardar_tarea,
                                 ]),
                      ]),
            borde=Tono.ACENTO,
        )

    def ficha_borrado():
        def confirmar(e):
            def trabajo():
                return tareas.eliminar(page, id_proyecto,
                                       usuario["id_usuario"], id_tarea)

            def al_terminar(_, fallo):
                if fallo:
                    avisar(page, f"No se pudo eliminar: {fallo}", Tono.PELIGRO)
                    return
                avisar(page, "Tarea eliminada.", Tono.TINTA)
                page.navigate(f"/tablero/{id_proyecto}")

            ejecutar_fuera_del_hilo(page, estado, boton_eliminar_tarea, "Eliminando...",
                                    trabajo, al_terminar)

        boton_eliminar_tarea = boton("Eliminar la tarea", confirmar,
                                     ft.Icons.DELETE_OUTLINE, peligro=True)

        return tarjeta(
            ft.Column(spacing=12, controls=[
                rotulo("Eliminar tarea"),
                texto("La tarea desaparecera del tablero para todo el equipo. "
                      "La conversacion se conserva, pero dejara de ser "
                      "accesible desde el tablero.", 13, color=Tono.PELIGRO),
                ft.Row(alignment=ft.MainAxisAlignment.END, spacing=6, controls=[
                    boton_discreto("Conservar",
                                   lambda e: cancelar_borrado_tarea()),
                    boton_eliminar_tarea,
                ]),
            ]),
            borde=Tono.PELIGRO, fondo=Tono.PELIGRO_FONDO,
        )

    def construir_ficha():
        if estado_vista["borrando"]:
            ficha.content = ficha_borrado()
        elif estado_vista["editando"]:
            ficha.content = ficha_edicion()
        else:
            ficha.content = ficha_lectura()

    def empezar_edicion_tarea(e=None):
        estado_vista["editando"] = True
        estado_vista["borrando"] = False
        repintar()

    def cancelar_edicion_tarea():
        estado_vista["editando"] = False
        repintar()

    def pedir_borrado_tarea(e=None):
        estado_vista["borrando"] = True
        estado_vista["editando"] = False
        repintar()

    def cancelar_borrado_tarea():
        estado_vista["borrando"] = False
        repintar()

    # --- hilo de comentarios

    def burbuja(comentario):
        clave = comentario["SK"]
        autor = comentario.get("nombre_autor", "Alguien")
        es_mio = comentario.get("id_autor") == usuario["id_usuario"]
        suprimido = comentarios.esta_eliminado(comentario)

        cabecera = ft.Row(spacing=8,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[
                              circulo(autor, 13),
                              texto(autor, 12.5, ft.FontWeight.W_600),
                              texto(tiempo_relativo(comentario.get("fecha")), 11,
                                    color=Tono.TINTA_TENUE,
                                    tooltip=str(comentario.get("fecha", ""))[:19]
                                    .replace("T", " ")),
                          ])
        if comentarios.fue_editado(comentario) and not suprimido:
            cabecera.controls.append(texto("· editado", 11,
                                           color=Tono.TINTA_TENUE))

        if suprimido:
            return ft.Container(
                padding=ft.Padding.all(13), bgcolor=Tono.BORDE_TENUE,
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(spacing=5, controls=[
                    cabecera,
                    ft.Text(value=comentarios.LEYENDA_ELIMINADO, size=12.5,
                            italic=True, color=Tono.TINTA_TENUE),
                ]))

        if edicion["clave"] == clave:
            editor = campo("", multiline=True, min_lines=2, max_lines=6,
                           value=edicion["borrador"], autofocus=True)
            editor.on_change = lambda e: edicion.__setitem__("borrador",
                                                             e.control.value)
            return ft.Container(
                padding=ft.Padding.all(13), bgcolor=Tono.ACENTO_FONDO,
                border=ft.Border.all(1, Tono.ACENTO),
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        cabecera, editor,
                        ft.Row(alignment=ft.MainAxisAlignment.END, spacing=6,
                               controls=[
                                   boton_discreto("Cancelar",
                                                  lambda e: cancelar_edicion()),
                                   boton("Guardar",
                                         lambda e, c=clave: guardar_edicion(c),
                                         ft.Icons.CHECK),
                               ]),
                    ]))

        if confirmando["clave"] == clave:
            return ft.Container(
                padding=ft.Padding.all(13), bgcolor=Tono.PELIGRO_FONDO,
                border=ft.Border.all(1, Tono.PELIGRO),
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(spacing=10, controls=[
                    cabecera,
                    texto("Se eliminara el contenido. Quedara constancia de que "
                          "usted participo, pero el texto no se podra recuperar.",
                          12.5, color=Tono.PELIGRO),
                    ft.Row(alignment=ft.MainAxisAlignment.END, spacing=6,
                           controls=[
                               boton_discreto("Conservar",
                                              lambda e: cancelar_confirmacion()),
                               boton("Eliminar",
                                     lambda e, c=clave: confirmar_borrado(c),
                                     ft.Icons.DELETE_OUTLINE, peligro=True),
                           ]),
                ]))

        acciones = []
        if es_mio:
            acciones = [
                boton_icono(ft.Icons.EDIT_OUTLINED, "Editar",
                            lambda e, c=clave, t=comentario.get("texto", ""):
                            empezar_edicion(c, t)),
                boton_icono(ft.Icons.DELETE_OUTLINE, "Eliminar",
                            lambda e, c=clave: pedir_confirmacion(c)),
            ]

        return ft.Container(
            padding=ft.Padding.only(left=13, top=11, right=6, bottom=11),
            bgcolor=Tono.ACENTO_FONDO if es_mio else Tono.SUPERFICIE,
            border=ft.Border.all(1, Tono.ACENTO_BORDE if es_mio else Tono.BORDE),
            border_radius=ft.BorderRadius.all(12),
            content=ft.Column(spacing=6, controls=[
                ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[cabecera, ft.Row(spacing=0, controls=acciones)]),
                ft.Container(padding=ft.Padding.only(right=8),
                             content=texto(comentario.get("texto", ""), 13.5,
                                           selectable=True)),
            ]))

    def construir_hilo():
        hilo.controls.clear()
        try:
            historial = comentarios.listar(id_proyecto, usuario["id_usuario"],
                                           id_tarea)
        except Exception as fallo:  # noqa: BLE001
            hilo.controls.append(texto(f"No se pudo cargar el hilo: {fallo}", 12,
                                       color=Tono.PELIGRO))
            return
        if not historial:
            hilo.controls.append(vacio("Nadie ha comentado todavia.",
                                       ft.Icons.CHAT_BUBBLE_OUTLINE))
        for comentario in historial:
            hilo.controls.append(burbuja(comentario))

    def repintar():
        construir_ficha()
        construir_hilo()
        page.update()

    def refrescar_desde_evento():
        """Refresca el detalle y avisa si el cambio vino de otra persona."""
        try:
            try:
                antes = len(comentarios.listar(id_proyecto,
                                               usuario["id_usuario"], id_tarea))
            except Exception:  # noqa: BLE001
                antes = None

            nueva = cargar_tarea()
            if nueva is not None:
                estado_vista["tarea"] = nueva
            construir_ficha()
            construir_hilo()
            page.update()
            medicion.marcar_recepcion(id_proyecto, usuario["id_usuario"])

            if antes is None:
                return
            ahora = len(comentarios.listar(id_proyecto, usuario["id_usuario"],
                                           id_tarea))
            if ahora > antes:
                titulo_tarea_actual = (estado_vista["tarea"] or {}).get(
                    "titulo", "la tarea")
                frase = f"Nuevo comentario en «{titulo_tarea_actual}»"
                avisar(page, frase, Tono.ACENTO)
                vibrar(estado)
                notificaciones.mostrar(estado.bucle, estado.notificador,
                                       "SyncTask CUN", frase)
        except Exception:  # noqa: BLE001
            pass

    def empezar_edicion(clave, texto_actual):
        confirmando["clave"] = None
        edicion["clave"] = clave
        edicion["borrador"] = texto_actual
        repintar()

    def cancelar_edicion():
        edicion["clave"] = None
        edicion["borrador"] = ""
        repintar()

    def guardar_edicion(clave):
        contenido = (edicion["borrador"] or "").strip()
        if not contenido:
            avisar(page, "El comentario no puede quedar vacio.", Tono.PELIGRO)
            return
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"],
                                "comentario_editado")
        try:
            comentarios.editar(page, id_proyecto, usuario["id_usuario"], clave,
                               contenido)
        except comentarios.ErrorDeAutoria as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
        except Exception as fallo:  # noqa: BLE001
            avisar(page, f"No se pudo editar: {fallo}", Tono.PELIGRO)
            return
        edicion["clave"] = None
        edicion["borrador"] = ""
        repintar()

    def pedir_confirmacion(clave):
        edicion["clave"] = None
        confirmando["clave"] = clave
        repintar()

    def cancelar_confirmacion():
        confirmando["clave"] = None
        repintar()

    def confirmar_borrado(clave):
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"],
                                "comentario_eliminado")
        try:
            comentarios.eliminar(page, id_proyecto, usuario["id_usuario"], clave)
        except comentarios.ErrorDeAutoria as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
        except Exception as fallo:  # noqa: BLE001
            avisar(page, f"No se pudo eliminar: {fallo}", Tono.PELIGRO)
            return
        confirmando["clave"] = None
        repintar()

    def enviar(e):
        if not nuevo.value or not nuevo.value.strip():
            return
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"], "comentario")
        contenido = nuevo.value

        def trabajo():
            return comentarios.agregar(page, id_proyecto, usuario["id_usuario"],
                                       usuario["nombre"], id_tarea, contenido)

        def al_terminar(_, fallo):
            if fallo:
                avisar(page, f"No se pudo publicar: {fallo}", Tono.PELIGRO)
                return
            nuevo.value = ""
            construir_hilo()

        ejecutar_fuera_del_hilo(page, estado, boton_publicar, "Publicando...", trabajo,
                                al_terminar)

    boton_publicar = boton("Publicar", enviar, ft.Icons.SEND_OUTLINED)

    construir_ficha()
    construir_hilo()

    # El detalle tambien escucha el tema del proyecto: si un companero comenta
    # la misma tarea mientras esta pantalla esta abierta, el hilo se actualiza.
    soltar_suscripcion(page, estado)
    tareas.suscribir(page, id_proyecto, lambda mensaje: refrescar_desde_evento())
    estado.suscrito_a = id_proyecto
    estado.refrescar = refrescar_desde_evento

    return ft.View(
        route=f"/tarea/{id_proyecto}/{id_tarea}",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, "Detalle de la tarea", con_volver=True,
                     ruta_de_regreso=f"/tablero/{id_proyecto}",
                     acciones=[
                         boton_icono(ft.Icons.EDIT_OUTLINED, "Editar la tarea",
                                     empezar_edicion_tarea),
                         boton_icono(ft.Icons.DELETE_OUTLINE,
                                     "Eliminar la tarea", pedir_borrado_tarea),
                     ]),
        controls=[
            centrar(estado, [
                ficha,
                ft.Container(height=2),
                rotulo("Conversacion"),
                hilo,
                ft.Container(height=2),
                tarjeta(ft.Column(
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        nuevo,
                        ft.Row(alignment=ft.MainAxisAlignment.END,
                               controls=[boton_publicar]),
                    ]), relleno=14),
            ])
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ===========================================================================
# Enrutador
# ===========================================================================


def main(page: ft.Page):
    estado = Estado()
    try:
        estado.bucle = asyncio.get_running_loop()
    except RuntimeError:
        estado.bucle = None

    page.title = "SyncTask CUN"
    page.bgcolor = Tono.FONDO
    page.theme = ft.Theme(color_scheme=ft.ColorScheme(
        primary=Tono.ACENTO, surface=Tono.SUPERFICIE, on_surface=Tono.TINTA))
    # Se fija el tema claro en lugar de seguir al sistema: en modo oscuro del
    # navegador varios textos quedaban por debajo del contraste exigido por la
    # WCAG 2.1 nivel AA (defecto D-07).
    page.theme_mode = ft.ThemeMode.LIGHT

    def al_cambiar_conexion(e=None):
        """REQ-10: avisar de la perdida de red y de su recuperacion."""
        try:
            tipos = conectividad.get_connectivity()
            perdida = (not tipos) or all(t == ft.ConnectivityType.NONE
                                         for t in tipos)
        except Exception:  # noqa: BLE001
            return
        if perdida == estado.sin_conexion:
            return
        estado.sin_conexion = perdida
        if estado.banda is not None:
            estado.banda.visible = perdida
        if not perdida:
            avisar(page, "Conexion restablecida.", Tono.EXITO)
            # Al volver la red, lo que se ve puede estar desactualizado: se
            # vuelve a leer en lugar de esperar al proximo evento ajeno.
            if estado.refrescar:
                estado.refrescar()
        try:
            page.update()
        except Exception:  # noqa: BLE001
            pass

    preferencias = ft.SharedPreferences()
    portapapeles = ft.Clipboard()
    conectividad = ft.Connectivity(on_change=al_cambiar_conexion)
    vibracion = ft.HapticFeedback()
    # El servicio de notificaciones solo se engancha cuando quien se conecta es
    # la aplicacion instalada en un telefono, nunca un navegador.
    #
    # El motivo es que una extension de Flet tiene dos mitades: la de Python,
    # que corre aqui, y la de Dart, que va compilada dentro del cliente. El APK
    # la lleva porque se declara en apk/pyproject.toml. El cliente web es un
    # paquete ya construido que no la lleva ni puede llevarla, asi que
    # declararle un servicio que no sabe crear es pedirle algo imposible.
    #
    # La condicion esta escrita para fallar hacia el lado seguro: si no se
    # puede confirmar que el cliente es nativo, no se engancha nada. Se pierde
    # la notificacion del sistema —una comodidad— pero el aviso dentro de la
    # pantalla sigue saliendo igual, y eso es lo que no puede fallar.
    en_telefono = (not page.web) and page.platform in (
        ft.PagePlatform.ANDROID,
        ft.PagePlatform.ANDROID_TV,
        ft.PagePlatform.IOS,
    )
    notificador = notificaciones.crear_servicio() if en_telefono else None

    servicios = [preferencias, portapapeles, conectividad, vibracion]
    if notificador is not None:
        servicios.append(notificador)

    estado.preferencias = preferencias
    estado.portapapeles = portapapeles
    estado.vibracion = vibracion
    estado.notificador = notificador
    notificaciones.preparar(estado.bucle, notificador)

    def mostrar(vista):
        # Los servicios se enganchan a cada vista porque el enrutador sustituye
        # la vista raiz en cada navegacion; son siempre los mismos objetos, de
        # modo que lo guardado en el dispositivo sobrevive al cambio.
        vista.services = servicios
        page.views = [vista]
        page.update()

    def al_cambiar_de_ruta(e=None):
        """Unico punto que modifica page.views.

        Admite ser invocado sin evento. Hace falta para el primer pintado:
        push_route() le pide al navegador que cambie de direccion y es el
        navegador quien devuelve el evento, de modo que pedir la ruta en la
        que ya se esta no produce ningun evento y la pantalla quedaria en
        blanco. El arranque llama a esta funcion directamente.
        """
        ruta = page.route or "/"
        partes = [p for p in ruta.split("/") if p]

        # Ninguna pantalla distinta del ingreso y el registro se construye sin
        # usuario autenticado: una direccion escrita a mano no debe conceder
        # acceso (REQ-07, REQ-11).
        if not estado.usuario and ruta not in ("/", "/registro"):
            soltar_suscripcion(page, estado)
            page.navigate("/")
            return

        vista = None
        if ruta == "/registro":
            soltar_suscripcion(page, estado)
            vista = vista_registro(page, estado)
        elif ruta == "/proyectos":
            soltar_suscripcion(page, estado)
            vista = vista_proyectos(page, estado)
        elif len(partes) == 2 and partes[0] == "tablero":
            vista = vista_tablero(page, estado, partes[1])
        elif len(partes) == 3 and partes[0] == "tarea":
            vista = vista_tarea(page, estado, partes[1], partes[2])
        else:
            soltar_suscripcion(page, estado)
            vista = (vista_proyectos(page, estado) if estado.usuario
                     else vista_ingreso(page, estado))

        if vista is None:
            # La pantalla rechazo construirse, casi siempre por falta de
            # permiso. Se devuelve al usuario a terreno seguro, navegando para
            # que la direccion acompane a lo que se muestra.
            soltar_suscripcion(page, estado)
            if ruta != "/proyectos":
                page.navigate("/proyectos")
                return
            vista = vista_proyectos(page, estado)

        mostrar(vista)

    def al_volver(e):
        destino = "/proyectos" if estado.usuario else "/"
        if estado.id_proyecto and page.route.startswith("/tarea/"):
            destino = f"/tablero/{estado.id_proyecto}"
        page.navigate(destino)

    def al_desconectar(e):
        """El servidor se entera de que el cliente se fue (REQ-10).

        Lo unico util que se puede hacer aqui es soltar la suscripcion: no hay
        por donde avisar a una pantalla que ya no esta escuchando.
        """
        estado.sin_conexion = True
        soltar_suscripcion(page, estado)

    def al_reconectar(e):
        """La conexion volvio (REQ-10).

        Este es el mecanismo que de verdad sostiene el requisito. Mientras no
        hay red, el servidor no puede pintar nada; cuando vuelve, este evento
        si llega, y entonces hay dos cosas que hacer: avisar al usuario de que
        ya esta en linea, y releer el tablero, porque lo que tiene en pantalla
        puede llevar minutos desactualizado y esperar al proximo cambio ajeno
        lo dejaria mintiendo.
        """
        if not estado.sin_conexion:
            return
        estado.sin_conexion = False
        if estado.banda is not None:
            estado.banda.visible = False
        try:
            avisar(page, "Conexion restablecida. El tablero esta al dia.",
                   Tono.EXITO)
            al_cambiar_de_ruta()
        except Exception:  # noqa: BLE001
            pass

    page.on_route_change = al_cambiar_de_ruta
    page.on_view_pop = al_volver
    page.on_disconnect = al_desconectar
    page.on_connect = al_reconectar

    # Primer pintado. No se navega: la ruta ya es la correcta —sea "/" o un
    # enlace directo a un tablero— y pedirle al navegador que vaya adonde ya
    # esta no genera ningun evento.
    al_cambiar_de_ruta()

    # Sesion recordada. Va despues del primer pintado porque consultar el
    # almacenamiento del dispositivo exige que los servicios esten montados, y
    # solo lo estan una vez que la vista existe. Si hay sesion valida se
    # reencamina; si no, el usuario ya esta viendo la pantalla de ingreso.
    if not estado.usuario:
        try:
            recordado = recordar.recuperar(preferencias)
        except Exception:  # noqa: BLE001
            recordado = None
        if recordado:
            estado.usuario = recordado
            destino = page.route or "/"
            if destino in ("/", "/registro"):
                page.navigate("/proyectos")
            else:
                al_cambiar_de_ruta()


if __name__ == "__main__":
    ft.run(
        main,
        host="0.0.0.0",
        port=8550,
        view=ft.AppView.WEB_BROWSER,
        route_url_strategy=ft.RouteUrlStrategy.HASH,
        assets_dir="assets",
    )
