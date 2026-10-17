"""SyncTask CUN - aplicacion completa (REQ-01 a REQ-12).

Arranque en el servidor: lo gobierna systemd.

    sudo systemctl restart synctask
    journalctl -u synctask -n 50 --no-pager

Cinco decisiones gobiernan este archivo y conviene conocerlas antes de leerlo:

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
"""

import re
from datetime import datetime, timezone

import flet as ft

from modulos import comentarios, medicion, proyectos, sesion, tareas

# ===========================================================================
# Sistema visual
# ===========================================================================
#
# Una paleta corta y deliberada. El fondo es un gris calido en lugar de blanco
# puro porque el blanco puro sobre una pantalla de telefono a pleno sol cansa
# la vista, y porque deja que las tarjetas blancas se distingan sin necesidad
# de sombras. Hay un solo color de acento: cuando todo puede destacar, nada
# destaca, y el acento se reserva para la accion principal de cada pantalla.
# Los tres colores de estado son los unicos que compiten con el, y lo hacen en
# superficies pequenas.


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
    PELIGRO = "#DC2626"
    PELIGRO_FONDO = "#FEF2F2"
    EXITO = "#15803D"


ESTADOS = ("Pendiente", "En progreso", "Terminado")

# Cada estado con su tinta y su fondo. Se guardan juntos para que no exista la
# posibilidad de emparejar mal un color de texto con un fondo y producir un
# contraste insuficiente.
ESTILO_ESTADO = {
    "Pendiente": ("#B45309", "#FEF3C7"),
    "En progreso": ("#1D4ED8", "#DBEAFE"),
    "Terminado": ("#15803D", "#DCFCE7"),
}

# Reparto de la columna de contenido sobre la rejilla de doce columnas de
# ResponsiveRow. En un telefono (xs, por debajo de 576 px) ocupa las doce; a
# medida que la pantalla crece toma menos, de modo que en un monitor el texto
# no se estira hasta volverse incomodo de leer. Flet lo recalcula solo al
# cambiar el tamano de la ventana o al girar el telefono, sin reconstruir la
# pantalla, que es lo que borraria un formulario a medio escribir cuando se
# abre el teclado.
COLUMNAS = {"xs": 12, "sm": 10, "md": 8, "lg": 6, "xl": 4}

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
        self.filtro = "Todas"
        self.refrescar = None


# ===========================================================================
# Piezas reutilizables
# ===========================================================================


def iniciales(nombre):
    """Dos letras a partir de un nombre, para el circulo identificador.

    Un circulo con iniciales ocupa lo mismo que un icono generico y dice algo:
    permite distinguir de un vistazo quien escribio cada comentario sin leer el
    nombre completo.
    """
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
    """Convierte una marca ISO en algo legible: "hace 5 min".

    Una fecha completa en cada comentario es ruido: lo que el lector quiere
    saber es si esto se dijo hace un momento o la semana pasada. La fecha
    exacta se conserva en el atributo y se muestra al pasar el cursor.
    """
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
        horas = int(segundos // 3600)
        return f"hace {horas} h"
    dias = int(segundos // 86400)
    if dias == 1:
        return "ayer"
    if dias < 7:
        return f"hace {dias} dias"
    return momento.strftime("%d/%m/%Y")


def avisar(page, texto, color=None):
    """Mensaje efimero al pie de la pantalla.

    Se construye uno nuevo en cada llamada porque show_dialog rechaza un
    dialogo que ya esta en la pila.
    """
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(value=texto, color=ft.Colors.WHITE, size=13),
            bgcolor=color or Tono.TINTA,
            duration=3500,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.Margin.all(12),
            shape=ft.RoundedRectangleBorder(radius=10),
        )
    )


def centrar(controles):
    """Columna de contenido centrada que se adapta al ancho de la pantalla."""
    return ft.ResponsiveRow(
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            ft.Container(
                col=COLUMNAS,
                padding=ft.Padding.symmetric(horizontal=18, vertical=18),
                content=ft.Column(
                    controls=controles,
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            )
        ],
    )


def tarjeta(contenido, relleno=16):
    """Superficie blanca con borde fino, sin sombra.

    La sombra es el recurso facil para separar una tarjeta del fondo; un borde
    de un pixel hace lo mismo con menos ruido visual y se comporta mejor en
    pantallas pequenas, donde varias sombras juntas ensucian la lectura.
    """
    return ft.Container(
        padding=ft.Padding.all(relleno),
        bgcolor=Tono.SUPERFICIE,
        border=ft.Border.all(1, Tono.BORDE),
        border_radius=ft.BorderRadius.all(14),
        content=contenido,
    )


def texto(valor, tamano=13, peso=ft.FontWeight.W_400, color=None, **extra):
    return ft.Text(
        value=valor, size=tamano, weight=peso, color=color or Tono.TINTA, **extra
    )


def rotulo(valor):
    """Titulo de seccion: pequeno, espaciado, en tinta suave."""
    return ft.Text(
        value=valor.upper(),
        size=10,
        weight=ft.FontWeight.W_600,
        color=Tono.TINTA_TENUE,
    )


def campo(etiqueta, **extra):
    """Campo de texto con el mismo tratamiento en toda la aplicacion."""
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
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_500),
        ),
    )


def circulo(nombre, medida=16):
    return ft.CircleAvatar(
        radius=medida,
        bgcolor=color_de_persona(nombre),
        content=ft.Text(
            value=iniciales(nombre),
            size=medida * 0.62,
            weight=ft.FontWeight.W_700,
            color=ft.Colors.WHITE,
        ),
    )


def insignia_estado(nombre_estado, compacta=False):
    tinta, fondo = ESTILO_ESTADO.get(nombre_estado, (Tono.TINTA_SUAVE, Tono.BORDE_TENUE))
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=9, vertical=3),
        bgcolor=fondo,
        border_radius=ft.BorderRadius.all(20),
        content=ft.Text(
            value=nombre_estado,
            size=10 if compacta else 11,
            weight=ft.FontWeight.W_600,
            color=tinta,
        ),
    )


def vacio(mensaje, icono=ft.Icons.INBOX_OUTLINED):
    """Estado vacio: una pantalla sin datos tambien debe decir algo."""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=20, vertical=40),
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icono, size=28, color=Tono.TINTA_TENUE),
                ft.Text(
                    value=mensaje,
                    size=13,
                    color=Tono.TINTA_SUAVE,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
        ),
    )


def marca_sync(medida=38):
    """Distintivo de la aplicacion: dos letras en un cuadrado redondeado."""
    return ft.Container(
        width=medida,
        height=medida,
        bgcolor=Tono.ACENTO,
        border_radius=ft.BorderRadius.all(medida * 0.3),
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            value="ST",
            size=medida * 0.42,
            weight=ft.FontWeight.W_700,
            color=ft.Colors.WHITE,
        ),
    )


def barra(page, estado, titulo_texto, con_volver=False, ruta_de_regreso=None):
    acciones = []
    if estado.usuario:
        acciones.append(
            ft.IconButton(
                icon=ft.Icons.LOGOUT,
                icon_size=19,
                icon_color=Tono.TINTA_SUAVE,
                tooltip="Cerrar sesion",
                on_click=lambda e: cerrar_sesion(page, estado),
            )
        )
    principal = None
    if con_volver:
        principal = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            icon_size=19,
            icon_color=Tono.TINTA,
            tooltip="Volver",
            on_click=lambda e: page.navigate(ruta_de_regreso or "/proyectos"),
        )
    return ft.AppBar(
        leading=principal,
        automatically_imply_leading=False,
        title=ft.Text(
            value=titulo_texto,
            size=16,
            weight=ft.FontWeight.W_600,
            color=Tono.TINTA,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        center_title=False,
        bgcolor=Tono.SUPERFICIE,
        elevation=0,
        toolbar_height=58,
        actions=acciones,
    )


def cerrar_sesion(page, estado):
    """Cierra la sesion y suelta la suscripcion abierta (REQ-11)."""
    soltar_suscripcion(page, estado)
    estado.usuario = None
    estado.id_proyecto = None
    estado.proyecto = None
    page.navigate("/")


def soltar_suscripcion(page, estado):
    if estado.suscrito_a:
        try:
            tareas.cancelar_suscripcion(page, estado.suscrito_a)
        except Exception:
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

    def mostrar(mensaje):
        error.value = mensaje
        error.visible = bool(mensaje)
        page.update()

    def entrar(e):
        mostrar("")
        if not correo.value or not clave.value:
            mostrar("Escriba su correo y su contrasena.")
            return
        try:
            usuario = sesion.autenticar(correo.value.strip(), clave.value)
        except Exception as fallo:
            mostrar(f"No se pudo verificar la cuenta: {fallo}")
            return
        if not usuario:
            # Un mensaje unico para correo inexistente y contrasena incorrecta:
            # distinguirlos permitiria averiguar que correos estan registrados.
            mostrar("El correo o la contrasena no coinciden.")
            return
        estado.usuario = usuario
        page.navigate("/proyectos")

    clave.on_submit = entrar

    return ft.View(
        route="/",
        bgcolor=Tono.FONDO,
        controls=[
            centrar(
                [
                    ft.Container(height=30),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        controls=[marca_sync(46)],
                    ),
                    ft.Container(height=2),
                    ft.Text(
                        value="SyncTask CUN",
                        size=26,
                        weight=ft.FontWeight.W_700,
                        color=Tono.TINTA,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        value="Las tareas del equipo, iguales para todos y al instante",
                        size=13,
                        color=Tono.TINTA_SUAVE,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Container(height=10),
                    tarjeta(
                        ft.Column(
                            spacing=14,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[correo, clave, error,
                                      boton("Ingresar", entrar, ft.Icons.ARROW_FORWARD)],
                        ),
                        relleno=18,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=4,
                        controls=[
                            ft.Text(value="No tiene cuenta?", size=13,
                                    color=Tono.TINTA_SUAVE),
                            boton_discreto(
                                "Registrarse",
                                lambda e: page.navigate("/registro"),
                                color=Tono.ACENTO,
                            ),
                        ],
                    ),
                ]
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
    repetir = campo("Repita la contrasena", password=True, can_reveal_password=True)
    autoriza = ft.Checkbox(
        label="Autorizo el tratamiento de mis datos personales",
        value=False,
        active_color=Tono.ACENTO,
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
                ft.Row(
                    spacing=7,
                    controls=[
                        ft.Icon(ft.Icons.SHIELD_OUTLINED, size=15, color=Tono.ACENTO),
                        ft.Text(value="Aviso de privacidad", size=12,
                                weight=ft.FontWeight.W_700, color=Tono.ACENTO),
                    ],
                ),
                ft.Text(
                    value=AVISO_DE_PRIVACIDAD,
                    size=11,
                    color="#4338CA",
                    selectable=True,
                ),
            ],
        ),
    )

    def mostrar(mensaje):
        error.value = mensaje
        error.visible = bool(mensaje)
        page.update()

    def registrar(e):
        mostrar("")
        if not nombre.value or not correo.value or not clave.value:
            mostrar("Complete el nombre, el correo y la contrasena.")
            return
        if clave.value != repetir.value:
            mostrar("Las dos contrasenas no coinciden.")
            return
        if len(clave.value) < 8:
            mostrar("La contrasena debe tener al menos ocho caracteres.")
            return
        if not autoriza.value:
            # REQ-08: sin autorizacion expresa no se crea la cuenta. La
            # comprobacion se repite en sesion.registrar_usuario, que es la que
            # cuenta; esta solo evita un viaje a la base de datos.
            mostrar("Debe autorizar el tratamiento de datos para continuar.")
            return
        try:
            usuario = sesion.registrar_usuario(
                nombre.value.strip(), correo.value.strip(), clave.value, True
            )
        except Exception as fallo:
            mostrar(str(fallo))
            return
        estado.usuario = usuario
        avisar(page, "Cuenta creada. Bienvenido.", Tono.EXITO)
        page.navigate("/proyectos")

    return ft.View(
        route="/registro",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, "Crear cuenta", con_volver=True, ruta_de_regreso="/"),
        controls=[
            centrar(
                [
                    tarjeta(
                        ft.Column(
                            spacing=14,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[nombre, correo, clave, repetir],
                        ),
                        relleno=18,
                    ),
                    aviso,
                    autoriza,
                    error,
                    boton("Crear mi cuenta", registrar, ft.Icons.PERSON_ADD_ALT),
                ]
            )
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
        except Exception as fallo:
            lista.controls.append(
                texto(f"No se pudieron cargar los proyectos: {fallo}", 12,
                      color=Tono.PELIGRO)
            )
            return
        if not mios:
            lista.controls.append(
                vacio("Todavia no pertenece a ningun proyecto.\nCree uno o use un codigo.",
                      ft.Icons.FOLDER_OPEN_OUTLINED)
            )
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
                                width=38,
                                height=38,
                                bgcolor=color_de_persona(nombre_proyecto),
                                border_radius=ft.BorderRadius.all(11),
                                alignment=ft.Alignment.CENTER,
                                content=ft.Text(
                                    value=iniciales(nombre_proyecto),
                                    size=14,
                                    weight=ft.FontWeight.W_700,
                                    color=ft.Colors.WHITE,
                                ),
                            ),
                            ft.Column(
                                spacing=2,
                                expand=True,
                                controls=[
                                    texto(nombre_proyecto, 14, ft.FontWeight.W_600,
                                          max_lines=1,
                                          overflow=ft.TextOverflow.ELLIPSIS),
                                    texto(
                                        "Administrador" if es_admin else "Integrante",
                                        11, color=Tono.TINTA_TENUE,
                                    ),
                                ],
                            ),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20,
                                    color=Tono.TINTA_TENUE),
                        ],
                    ),
                )
            )

    def unirse(e):
        try:
            proyecto = proyectos.unirse(codigo.value, estado.usuario)
        except proyectos.ErrorDeCodigo as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo vincular: {fallo}", Tono.PELIGRO)
            return
        codigo.value = ""
        avisar(page, f"Se vinculo a {proyecto.get('nombre', 'el proyecto')}.", Tono.EXITO)
        page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

    def crear(e):
        try:
            proyecto = proyectos.crear(nombre.value, descripcion.value, estado.usuario)
        except ValueError as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo crear el proyecto: {fallo}", Tono.PELIGRO)
            return
        nombre.value = ""
        descripcion.value = ""
        avisar(page, f"Proyecto creado. Codigo: {proyecto.get('codigo_acceso', '')}",
               Tono.EXITO)
        page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

    pintar_lista()

    return ft.View(
        route="/proyectos",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado,
                     f"Hola, {estado.usuario['nombre'].split()[0]}"),
        controls=[
            centrar(
                [
                    rotulo("Mis proyectos"),
                    lista,
                    ft.Container(height=6),
                    rotulo("Unirme con un codigo"),
                    tarjeta(
                        ft.Column(
                            spacing=12,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[codigo,
                                      boton("Unirme", unirse, ft.Icons.GROUP_ADD_OUTLINED,
                                            principal=False)],
                        )
                    ),
                    ft.Container(height=6),
                    rotulo("Crear un proyecto nuevo"),
                    tarjeta(
                        ft.Column(
                            spacing=12,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[nombre, descripcion,
                                      boton("Crear proyecto", crear, ft.Icons.ADD)],
                        )
                    ),
                ]
            )
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

    titulo_tarea = campo("Titulo de la tarea")
    descripcion_tarea = campo("Descripcion (opcional)", multiline=True, min_lines=2)
    fecha_tarea = campo("Fecha limite", hint_text="AAAA-MM-DD")
    responsable_tarea = ft.Dropdown(
        label="Responsable",
        options=[],
        border_color=Tono.BORDE,
        focused_border_color=Tono.ACENTO,
        border_radius=ft.BorderRadius.all(10),
        text_size=14,
        label_style=ft.TextStyle(size=13, color=Tono.TINTA_SUAVE),
        content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
    )

    datos = {"tareas": []}
    conteo = {}

    def integrantes():
        try:
            from datos import repositorio

            return repositorio.listar_integrantes(id_proyecto)
        except Exception:
            return []

    def cargar_responsables():
        responsable_tarea.options = [
            ft.DropdownOption(key=n, text=n)
            for n in sorted(
                {i.get("nombre_usuario") or i.get("nombre") or "Sin nombre"
                 for i in integrantes()}
            )
        ]
        if not responsable_tarea.value and responsable_tarea.options:
            responsable_tarea.value = responsable_tarea.options[0].key

    def tarjeta_de_tarea(tarea):
        id_tarea = tarea["SK"].split("#", 1)[1]
        estado_actual = tarea.get("estado", "Pendiente")
        n_comentarios = conteo.get(id_tarea, 0)
        responsable = tarea.get("responsable", "")

        destinos = [
            boton_discreto(
                d,
                lambda e, i=id_tarea, x=d: mover(i, x),
                color=ESTILO_ESTADO.get(d, (Tono.TINTA_SUAVE, ""))[0],
            )
            for d in ESTADOS
            if d != estado_actual
        ]

        pie = ft.Row(
            wrap=True,
            spacing=2,
            run_spacing=2,
            controls=destinos
            + [
                boton_discreto(
                    f"Comentarios ({n_comentarios})" if n_comentarios
                    else "Comentar",
                    lambda e, i=id_tarea: page.navigate(f"/tarea/{id_proyecto}/{i}"),
                    ft.Icons.CHAT_BUBBLE_OUTLINE,
                    color=Tono.ACENTO if n_comentarios else Tono.TINTA_SUAVE,
                )
            ],
        )

        return ft.Container(
            padding=ft.Padding.only(left=16, top=14, right=12, bottom=8),
            bgcolor=Tono.SUPERFICIE,
            border=ft.Border.all(1, Tono.BORDE),
            border_radius=ft.BorderRadius.all(14),
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        spacing=10,
                        controls=[
                            texto(tarea.get("titulo", "(sin titulo)"), 14.5,
                                  ft.FontWeight.W_600, expand=True, max_lines=3,
                                  overflow=ft.TextOverflow.ELLIPSIS),
                            insignia_estado(estado_actual, compacta=True),
                        ],
                    ),
                    ft.Row(
                        spacing=8,
                        wrap=True,
                        run_spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            circulo(responsable, 10) if responsable else ft.Container(),
                            texto(responsable or "Sin responsable", 11.5,
                                  color=Tono.TINTA_SUAVE),
                            ft.Container(width=3, height=3, bgcolor=Tono.TINTA_TENUE,
                                         border_radius=ft.BorderRadius.all(2))
                            if tarea.get("fecha_limite") else ft.Container(),
                            texto(tarea.get("fecha_limite", ""), 11.5,
                                  color=Tono.TINTA_SUAVE),
                        ],
                    ),
                    ft.Divider(height=1, thickness=1, color=Tono.BORDE_TENUE),
                    pie,
                ],
            ),
        )

    def recargar():
        datos["tareas"] = tareas.listar(id_proyecto, usuario["id_usuario"])
        conteo.clear()
        try:
            conteo.update(comentarios.contar_por_tarea(id_proyecto,
                                                       usuario["id_usuario"]))
        except Exception:
            pass

    def construir(actualizar):
        por_estado = {e: 0 for e in ESTADOS}
        for t in datos["tareas"]:
            clave = t.get("estado", "Pendiente")
            por_estado[clave] = por_estado.get(clave, 0) + 1

        filtros.controls.clear()
        for etiqueta in ("Todas",) + ESTADOS:
            cantidad = (len(datos["tareas"]) if etiqueta == "Todas"
                        else por_estado.get(etiqueta, 0))
            activo = estado.filtro == etiqueta
            tinta = (ft.Colors.WHITE if activo
                     else ESTILO_ESTADO.get(etiqueta, (Tono.TINTA_SUAVE, ""))[0])
            fondo = (Tono.TINTA if activo else Tono.SUPERFICIE)
            filtros.controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=13, vertical=8),
                    border_radius=ft.BorderRadius.all(20),
                    bgcolor=fondo,
                    border=ft.Border.all(1, Tono.TINTA if activo else Tono.BORDE),
                    ink=True,
                    on_click=lambda e, f=etiqueta: filtrar(f),
                    content=ft.Row(
                        spacing=6,
                        tight=True,
                        controls=[
                            ft.Text(value=etiqueta, size=12,
                                    weight=ft.FontWeight.W_600, color=tinta),
                            ft.Text(value=str(cantidad), size=12,
                                    weight=ft.FontWeight.W_400,
                                    color=tinta if activo else Tono.TINTA_TENUE),
                        ],
                    ),
                )
            )

        visibles = [t for t in datos["tareas"]
                    if estado.filtro == "Todas" or t.get("estado") == estado.filtro]
        orden = {e: n for n, e in enumerate(ESTADOS)}
        visibles.sort(key=lambda t: (orden.get(t.get("estado", "Pendiente"), 9),
                                     t.get("fecha_limite") or "9999"))

        lista.controls.clear()
        if not visibles:
            lista.controls.append(
                vacio("Todavia no hay tareas aqui."
                      if estado.filtro == "Todas"
                      else f"No hay tareas en {estado.filtro.lower()}.",
                      ft.Icons.TASK_ALT)
            )
        for t in visibles:
            lista.controls.append(tarjeta_de_tarea(t))

        if actualizar:
            page.update()

    def pintar():
        construir(actualizar=True)

    def filtrar(etiqueta):
        estado.filtro = etiqueta
        pintar()

    def refrescar_desde_evento():
        """Lo invoca el manejador de PubSub, en otro hilo."""
        try:
            recargar()
            pintar()
            # La medicion se toma aqui y no antes: el requisito habla de
            # cuando el companero *ve* el cambio, de modo que el cronometro
            # para cuando la pantalla ya esta reconstruida (REQ-12).
            medicion.marcar_recepcion(id_proyecto, usuario["id_usuario"])
        except Exception:
            # Una sesion que ya navego a otra pantalla no debe tumbar el
            # proceso por intentar actualizar controles ausentes.
            pass

    def mover(id_tarea, destino):
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"], "cambio_de_estado")
        try:
            tareas.cambiar_estado(page, id_proyecto, usuario["id_usuario"],
                                  id_tarea, destino)
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo mover la tarea: {fallo}", Tono.PELIGRO)
            return
        refrescar_desde_evento()

    def alternar_formulario(e):
        formulario.visible = not formulario.visible
        if formulario.visible:
            cargar_responsables()
        page.update()

    def guardar_tarea(e):
        if not titulo_tarea.value or not titulo_tarea.value.strip():
            avisar(page, "La tarea necesita un titulo.", Tono.PELIGRO)
            return
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"], "tarea_creada")
        try:
            tareas.crear(
                page, id_proyecto, usuario["id_usuario"],
                titulo=titulo_tarea.value.strip(),
                descripcion=(descripcion_tarea.value or "").strip(),
                responsable=responsable_tarea.value or usuario["nombre"],
                fecha_limite=(fecha_tarea.value or "").strip(),
            )
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo crear la tarea: {fallo}", Tono.PELIGRO)
            return
        titulo_tarea.value = ""
        descripcion_tarea.value = ""
        fecha_tarea.value = ""
        formulario.visible = False
        avisar(page, "Tarea creada.", Tono.EXITO)
        refrescar_desde_evento()

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
            fecha_tarea,
            descripcion_tarea,
            ft.Row(
                alignment=ft.MainAxisAlignment.END,
                spacing=6,
                controls=[
                    boton_discreto("Cancelar", alternar_formulario),
                    boton("Guardar", guardar_tarea, ft.Icons.CHECK),
                ],
            ),
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
    except Exception as fallo:
        avisar(page, f"No se pudieron cargar las tareas: {fallo}", Tono.PELIGRO)

    encabezado = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        wrap=True,
        run_spacing=12,
        controls=[
            ft.Column(
                spacing=3,
                controls=[
                    rotulo("Codigo para invitar"),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=11, vertical=6),
                        bgcolor=Tono.SUPERFICIE,
                        border=ft.Border.all(1, Tono.BORDE),
                        border_radius=ft.BorderRadius.all(9),
                        content=ft.Text(
                            value=codigo_acceso,
                            size=15,
                            weight=ft.FontWeight.W_700,
                            color=Tono.TINTA,
                            selectable=True,
                        ),
                    ),
                ],
            ),
            boton("Nueva tarea", alternar_formulario, ft.Icons.ADD),
        ],
    )

    vista = ft.View(
        route=f"/tablero/{id_proyecto}",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, nombre_proyecto, con_volver=True,
                     ruta_de_regreso="/proyectos"),
        controls=[centrar([encabezado, formulario, filtros, lista])],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )
    # Se puebla antes de devolver la vista, para que el enrutador la entregue
    # ya pintada y no haya un parpadeo con el tablero vacio. No se llama a
    # page.update() porque la vista aun no esta montada en la pagina.
    construir(actualizar=False)
    return vista


# ===========================================================================
# Pantalla de detalle de tarea
# ===========================================================================


def vista_tarea(page, estado, id_proyecto, id_tarea):
    """Detalle de una tarea con su hilo de comentarios (REQ-06).

    El hilo admite edicion y supresion del comentario propio. Ambas se
    resuelven dentro de la misma burbuja en lugar de abrir un dialogo: en un
    telefono, un dialogo tapa la conversacion justo cuando el usuario necesita
    verla para decidir que escribir.
    """
    usuario = estado.usuario
    try:
        proyectos.obtener(id_proyecto, usuario)
    except tareas.ErrorDePermiso:
        avisar(page, "No pertenece a ese proyecto.", Tono.PELIGRO)
        return None

    la_tarea = None
    try:
        for t in tareas.listar(id_proyecto, usuario["id_usuario"]):
            if t["SK"].split("#", 1)[1] == id_tarea:
                la_tarea = t
                break
    except Exception as fallo:
        avisar(page, f"No se pudo cargar la tarea: {fallo}", Tono.PELIGRO)

    hilo = ft.Column(spacing=10)
    nuevo = campo("Escriba un comentario", multiline=True, min_lines=2, max_lines=6)

    # Que comentario se esta editando y cual espera confirmacion de borrado.
    # Viven fuera de construir_hilo() para que sobrevivan a una reconstruccion
    # provocada por el mensaje de otra sesion: si no, un companero que comenta
    # mientras usted edita le borraria el texto a medio escribir.
    edicion = {"clave": None, "borrador": ""}
    confirmando = {"clave": None}

    def burbuja(comentario):
        clave = comentario["SK"]
        autor = comentario.get("nombre_autor", "Alguien")
        es_mio = comentario.get("id_autor") == usuario["id_usuario"]
        suprimido = comentarios.esta_eliminado(comentario)

        cabecera = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                circulo(autor, 13),
                texto(autor, 12.5, ft.FontWeight.W_600),
                texto(tiempo_relativo(comentario.get("fecha")), 11,
                      color=Tono.TINTA_TENUE,
                      tooltip=str(comentario.get("fecha", ""))[:19].replace("T", " ")),
            ],
        )
        if comentarios.fue_editado(comentario) and not suprimido:
            cabecera.controls.append(
                texto("· editado", 11, color=Tono.TINTA_TENUE)
            )

        # --- comentario suprimido
        if suprimido:
            return ft.Container(
                padding=ft.Padding.all(13),
                bgcolor=Tono.BORDE_TENUE,
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(
                    spacing=5,
                    controls=[
                        cabecera,
                        ft.Text(
                            value=comentarios.LEYENDA_ELIMINADO,
                            size=12.5,
                            italic=True,
                            color=Tono.TINTA_TENUE,
                        ),
                    ],
                ),
            )

        # --- en edicion
        if edicion["clave"] == clave:
            editor = campo("", multiline=True, min_lines=2, max_lines=6,
                           value=edicion["borrador"], autofocus=True)

            def anotar(e):
                edicion["borrador"] = e.control.value

            editor.on_change = anotar
            return ft.Container(
                padding=ft.Padding.all(13),
                bgcolor=Tono.ACENTO_FONDO,
                border=ft.Border.all(1, Tono.ACENTO),
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        cabecera,
                        editor,
                        ft.Row(
                            alignment=ft.MainAxisAlignment.END,
                            spacing=6,
                            controls=[
                                boton_discreto("Cancelar", lambda e: cancelar_edicion()),
                                boton("Guardar", lambda e, c=clave: guardar_edicion(c),
                                      ft.Icons.CHECK),
                            ],
                        ),
                    ],
                ),
            )

        # --- esperando confirmacion de borrado
        if confirmando["clave"] == clave:
            return ft.Container(
                padding=ft.Padding.all(13),
                bgcolor=Tono.PELIGRO_FONDO,
                border=ft.Border.all(1, Tono.PELIGRO),
                border_radius=ft.BorderRadius.all(12),
                content=ft.Column(
                    spacing=10,
                    controls=[
                        cabecera,
                        texto("Se eliminara el contenido. Quedara constancia de que "
                              "usted participo, pero el texto no se podra recuperar.",
                              12.5, color=Tono.PELIGRO),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.END,
                            spacing=6,
                            controls=[
                                boton_discreto("Conservar",
                                               lambda e: cancelar_confirmacion()),
                                boton("Eliminar",
                                      lambda e, c=clave: confirmar_borrado(c),
                                      ft.Icons.DELETE_OUTLINE, peligro=True),
                            ],
                        ),
                    ],
                ),
            )

        # --- burbuja normal
        acciones = []
        if es_mio:
            acciones = [
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_size=15,
                    icon_color=Tono.TINTA_TENUE,
                    tooltip="Editar",
                    on_click=lambda e, c=clave, t=comentario.get("texto", ""):
                        empezar_edicion(c, t),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=15,
                    icon_color=Tono.TINTA_TENUE,
                    tooltip="Eliminar",
                    on_click=lambda e, c=clave: pedir_confirmacion(c),
                ),
            ]

        return ft.Container(
            padding=ft.Padding.only(left=13, top=11, right=6, bottom=11),
            bgcolor=Tono.ACENTO_FONDO if es_mio else Tono.SUPERFICIE,
            border=ft.Border.all(1, Tono.BORDE if not es_mio else "#E0E7FF"),
            border_radius=ft.BorderRadius.all(12),
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[cabecera, ft.Row(spacing=0, controls=acciones)],
                    ),
                    ft.Container(
                        padding=ft.Padding.only(right=8),
                        content=texto(comentario.get("texto", ""), 13.5,
                                      selectable=True),
                    ),
                ],
            ),
        )

    def construir_hilo():
        hilo.controls.clear()
        try:
            historial = comentarios.listar(id_proyecto, usuario["id_usuario"], id_tarea)
        except Exception as fallo:
            hilo.controls.append(texto(f"No se pudo cargar el hilo: {fallo}", 12,
                                       color=Tono.PELIGRO))
            return
        if not historial:
            hilo.controls.append(
                vacio("Nadie ha comentado todavia.\nSea el primero.",
                      ft.Icons.CHAT_BUBBLE_OUTLINE)
            )
        for comentario in historial:
            hilo.controls.append(burbuja(comentario))

    def refrescar_hilo():
        try:
            construir_hilo()
            page.update()
            medicion.marcar_recepcion(id_proyecto, usuario["id_usuario"])
        except Exception:
            pass

    def repintar():
        construir_hilo()
        page.update()

    # --- acciones sobre comentarios

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
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"], "comentario_editado")
        try:
            comentarios.editar(page, id_proyecto, usuario["id_usuario"], clave,
                               contenido)
        except comentarios.ErrorDeAutoria as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
        except Exception as fallo:
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
        except Exception as fallo:
            avisar(page, f"No se pudo eliminar: {fallo}", Tono.PELIGRO)
            return
        confirmando["clave"] = None
        repintar()

    def enviar(e):
        if not nuevo.value or not nuevo.value.strip():
            return
        medicion.marcar_emision(id_proyecto, usuario["id_usuario"], "comentario")
        try:
            comentarios.agregar(page, id_proyecto, usuario["id_usuario"],
                                usuario["nombre"], id_tarea, nuevo.value)
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except ValueError as fallo:
            avisar(page, str(fallo), Tono.PELIGRO)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo publicar: {fallo}", Tono.PELIGRO)
            return
        nuevo.value = ""
        repintar()

    construir_hilo()

    # El detalle tambien escucha el tema del proyecto: si un companero comenta
    # la misma tarea mientras esta pantalla esta abierta, el hilo se actualiza.
    soltar_suscripcion(page, estado)
    tareas.suscribir(page, id_proyecto, lambda mensaje: refrescar_hilo())
    estado.suscrito_a = id_proyecto
    estado.refrescar = refrescar_hilo

    estado_actual = (la_tarea or {}).get("estado", "Pendiente")
    responsable = (la_tarea or {}).get("responsable", "")

    ficha = tarjeta(
        ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=10,
                    controls=[
                        texto((la_tarea or {}).get("titulo", "Tarea"), 17,
                              ft.FontWeight.W_700, expand=True),
                        insignia_estado(estado_actual),
                    ],
                ),
                ft.Row(
                    spacing=8,
                    wrap=True,
                    run_spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        circulo(responsable, 10) if responsable else ft.Container(),
                        texto(responsable or "Sin responsable", 12,
                              color=Tono.TINTA_SUAVE),
                        texto((la_tarea or {}).get("fecha_limite", ""), 12,
                              color=Tono.TINTA_SUAVE),
                    ],
                ),
                ft.Divider(height=1, thickness=1, color=Tono.BORDE_TENUE),
                texto((la_tarea or {}).get("descripcion", "") or "Sin descripcion.",
                      13, color=Tono.TINTA_SUAVE, selectable=True),
            ],
        )
    )

    return ft.View(
        route=f"/tarea/{id_proyecto}/{id_tarea}",
        bgcolor=Tono.FONDO,
        appbar=barra(page, estado, "Detalle de la tarea", con_volver=True,
                     ruta_de_regreso=f"/tablero/{id_proyecto}"),
        controls=[
            centrar(
                [
                    ficha,
                    ft.Container(height=2),
                    rotulo("Conversacion"),
                    hilo,
                    ft.Container(height=2),
                    tarjeta(
                        ft.Column(
                            spacing=10,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[
                                nuevo,
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.END,
                                    controls=[
                                        boton("Publicar", enviar, ft.Icons.SEND_OUTLINED)
                                    ],
                                ),
                            ],
                        ),
                        relleno=14,
                    ),
                ]
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ===========================================================================
# Enrutador
# ===========================================================================


def main(page: ft.Page):
    estado = Estado()

    page.title = "SyncTask CUN"
    page.bgcolor = Tono.FONDO
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=Tono.ACENTO,
            surface=Tono.SUPERFICIE,
            on_surface=Tono.TINTA,
        )
    )
    # Se fija el tema claro en lugar de seguir al sistema: en modo oscuro del
    # navegador varios textos quedaban por debajo del contraste exigido por la
    # WCAG 2.1 nivel AA (defecto D-07).
    page.theme_mode = ft.ThemeMode.LIGHT

    def mostrar(vista):
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
        # acceso (REQ-07, REQ-11). Se navega en lugar de pintar el ingreso en
        # el sitio para que la direccion del navegador no siga anunciando una
        # pantalla que no se esta mostrando.
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
        # REQ-10: el cliente web de Flet reintenta la conexion por su cuenta.
        # Lo unico que corresponde aqui es soltar la suscripcion para no
        # acumular manejadores de una sesion que ya no esta.
        soltar_suscripcion(page, estado)

    page.on_route_change = al_cambiar_de_ruta
    page.on_view_pop = al_volver
    page.on_disconnect = al_desconectar

    # Primer pintado. No se navega: la ruta ya es la correcta —sea "/" o un
    # enlace directo a un tablero— y pedirle al navegador que vaya adonde ya
    # esta no genera ningun evento.
    al_cambiar_de_ruta()


if __name__ == "__main__":
    ft.run(
        main,
        host="0.0.0.0",
        port=8550,
        view=ft.AppView.WEB_BROWSER,
        route_url_strategy=ft.RouteUrlStrategy.HASH,
    )
