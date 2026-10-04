"""SyncTask CUN - aplicacion completa (REQ-01 a REQ-11).

Arranque manual, para pruebas:

    cd ~/synctask && source ~/venv/bin/activate && python3 main.py

Cuatro decisiones gobiernan todo el archivo y conviene conocerlas antes de
leerlo:

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
   distintos porque una perdio un mensaje. La base de datos es la unica
   fuente de verdad; el mensaje solo dice "vuelve a mirar".

4. La autorizacion no depende de la navegacion. Toda pantalla que lea datos de
   un proyecto pasa por _exigir_pertenencia, porque una direccion se puede
   escribir a mano y el enrutador no es un control de acceso (REQ-07).
"""

import flet as ft

from modulos import comentarios, proyectos, sesion, tareas

# --------------------------------------------------------------- presentacion

ESTADOS = ("Pendiente", "En progreso", "Terminado")

COLOR_DE_ESTADO = {
    "Pendiente": ft.Colors.AMBER_800,
    "En progreso": ft.Colors.BLUE_700,
    "Terminado": ft.Colors.GREEN_700,
}

SEMILLA = ft.Colors.INDIGO

# Reparto de la columna de contenido sobre la rejilla de doce columnas de
# ResponsiveRow. En un telefono (xs, por debajo de 576 px) ocupa las doce, es
# decir el ancho completo; a medida que la pantalla crece toma menos columnas,
# de modo que en un monitor el texto no se estira hasta volverse incomodo de
# leer. Flet recalcula esto solo al cambiar el tamano de la ventana o al girar
# el telefono: no hace falta reconstruir la pantalla, que es lo que borraria
# un formulario a medio escribir cuando se abre el teclado.
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


# ------------------------------------------------------------------- utilidad


def avisar(page, texto, color=ft.Colors.BLUE_GREY_900):
    """Muestra un mensaje efimero.

    Se construye un SnackBar nuevo en cada llamada porque show_dialog rechaza
    un dialogo que ya esta en la pila.
    """
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(value=texto, color=ft.Colors.WHITE),
            bgcolor=color,
            duration=4000,
        )
    )


def centrar(controles, scroll=True):
    """Coloca el contenido en una columna centrada que se adapta a la pantalla.

    La misma vista se abre en un telefono de 360 px y en un monitor de 1920.
    Un ancho fijo resuelve el monitor y rompe el telefono: la columna se sale
    por la derecha y el usuario pierde los botones, que es justo lo que pasaba
    antes de esta correccion. ResponsiveRow reparte el ancho disponible sobre
    una rejilla de doce columnas segun el tamano real de la ventana, de modo
    que no hay ninguna medida en pixeles que pueda quedarse corta.

    El desplazamiento vertical lo gobierna la vista, no esta columna: anidar
    una zona desplazable dentro de otra confunde el gesto de arrastre en un
    telefono.
    """
    return ft.ResponsiveRow(
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            ft.Container(
                col=COLUMNAS,
                padding=ft.Padding.symmetric(horizontal=16, vertical=16),
                content=ft.Column(
                    controls=controles,
                    spacing=14,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            )
        ],
    )


def titulo(texto, subtitulo=None):
    controles = [
        ft.Text(
            value=texto,
            size=24,
            weight=ft.FontWeight.BOLD,
            text_align=ft.TextAlign.CENTER,
        )
    ]
    if subtitulo:
        controles.append(
            ft.Text(
                value=subtitulo,
                size=13,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
            )
        )
    return ft.Column(
        controls=controles, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER
    )


def barra(page, estado, texto, con_volver=False, ruta_de_regreso=None):
    acciones = []
    if estado.usuario:
        acciones.append(
            ft.IconButton(
                icon=ft.Icons.LOGOUT,
                tooltip="Cerrar sesion",
                on_click=lambda e: cerrar_sesion(page, estado),
            )
        )
    principal = None
    if con_volver:
        principal = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            tooltip="Volver",
            on_click=lambda e: page.navigate(ruta_de_regreso or "/proyectos"),
        )
    return ft.AppBar(
        leading=principal,
        automatically_imply_leading=False,
        title=ft.Text(
            value=texto,
            size=17,
            weight=ft.FontWeight.W_600,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        center_title=False,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
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


# ------------------------------------------------------- pantalla de ingreso


def vista_ingreso(page, estado):
    correo = ft.TextField(
        label="Correo institucional",
        keyboard_type=ft.KeyboardType.EMAIL,
        autofocus=True,
    )
    clave = ft.TextField(label="Contrasena", password=True, can_reveal_password=True)
    error = ft.Text(value="", color=ft.Colors.ERROR, size=13, visible=False)

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
        controls=[
            centrar(
                [
                    ft.Container(height=24),
                    titulo("SyncTask CUN", "Tareas de equipo sincronizadas al instante"),
                    ft.Container(height=12),
                    correo,
                    clave,
                    error,
                    ft.Button(
                        content="Ingresar",
                        icon=ft.Icons.LOGIN,
                        on_click=entrar,
                        bgcolor=ft.Colors.INDIGO_600,
                        color=ft.Colors.WHITE,
                    ),
                    ft.TextButton(
                        content="No tengo cuenta, quiero registrarme",
                        on_click=lambda e: page.navigate("/registro"),
                    ),
                ]
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ------------------------------------------------------- pantalla de registro


def vista_registro(page, estado):
    nombre = ft.TextField(label="Nombre y apellido", autofocus=True)
    correo = ft.TextField(
        label="Correo institucional", keyboard_type=ft.KeyboardType.EMAIL
    )
    clave = ft.TextField(label="Contrasena", password=True, can_reveal_password=True)
    repetir = ft.TextField(
        label="Repita la contrasena", password=True, can_reveal_password=True
    )
    autoriza = ft.Checkbox(
        label="Autorizo el tratamiento de mis datos personales", value=False
    )
    error = ft.Text(value="", color=ft.Colors.ERROR, size=13, visible=False)

    aviso = ft.Container(
        padding=ft.Padding.all(12),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        border_radius=ft.BorderRadius.all(10),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text(
                    value="Aviso de privacidad",
                    size=13,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    value=AVISO_DE_PRIVACIDAD,
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
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
        avisar(page, "Cuenta creada. Bienvenido.", ft.Colors.GREEN_700)
        page.navigate("/proyectos")

    return ft.View(
        route="/registro",
        appbar=barra(page, estado, "Crear cuenta", con_volver=True, ruta_de_regreso="/"),
        controls=[
            centrar(
                [
                    nombre,
                    correo,
                    clave,
                    repetir,
                    aviso,
                    autoriza,
                    error,
                    ft.Button(
                        content="Crear mi cuenta",
                        icon=ft.Icons.PERSON_ADD,
                        on_click=registrar,
                        bgcolor=ft.Colors.INDIGO_600,
                        color=ft.Colors.WHITE,
                    ),
                ]
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ----------------------------------------------------- pantalla de proyectos


def vista_proyectos(page, estado):
    codigo = ft.TextField(
        label="Codigo de acceso",
        hint_text="Seis caracteres",
        capitalization=ft.TextCapitalization.CHARACTERS,
    )
    nombre = ft.TextField(label="Nombre del proyecto")
    descripcion = ft.TextField(label="Descripcion (opcional)", multiline=True, min_lines=2)
    lista = ft.Column(spacing=8)

    def pintar_lista():
        lista.controls.clear()
        try:
            mios = proyectos.listar_mios(estado.usuario)
        except Exception as fallo:
            lista.controls.append(
                ft.Text(value=f"No se pudieron cargar los proyectos: {fallo}", size=12)
            )
            return
        if not mios:
            lista.controls.append(
                ft.Text(
                    value="Todavia no pertenece a ningun proyecto. Cree uno o use un codigo.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )
            return
        for elemento in mios:
            id_proyecto = elemento.get("id_proyecto")
            lista.controls.append(
                ft.Card(
                    elevation=1,
                    content=ft.ListTile(
                        leading=ft.Icon(ft.Icons.FOLDER_SHARED, color=SEMILLA),
                        title=ft.Text(
                            value=elemento.get("nombre_proyecto", "Proyecto"),
                            weight=ft.FontWeight.W_600,
                        ),
                        subtitle=ft.Text(value=elemento.get("rol", ""), size=11),
                        trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                        on_click=lambda e, i=id_proyecto: page.navigate(f"/tablero/{i}"),
                    ),
                )
            )

    def unirse(e):
        try:
            proyecto = proyectos.unirse(codigo.value, estado.usuario)
        except proyectos.ErrorDeCodigo as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo vincular: {fallo}", ft.Colors.RED_700)
            return
        codigo.value = ""
        avisar(page, f"Se vinculo a {proyecto.get('nombre', 'el proyecto')}.", ft.Colors.GREEN_700)
        page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

    def crear(e):
        try:
            proyecto = proyectos.crear(nombre.value, descripcion.value, estado.usuario)
        except ValueError as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo crear el proyecto: {fallo}", ft.Colors.RED_700)
            return
        nombre.value = ""
        descripcion.value = ""
        avisar(
            page,
            f"Proyecto creado. Codigo para invitar: {proyecto.get('codigo_acceso', '')}",
            ft.Colors.GREEN_700,
        )
        page.navigate("/tablero/" + proyecto["PK"].split("#", 1)[1])

    pintar_lista()

    def seccion(titulo_texto, controles):
        return ft.Container(
            padding=ft.Padding.all(14),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(12),
            content=ft.Column(
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Text(value=titulo_texto, size=13, weight=ft.FontWeight.BOLD)
                ]
                + controles,
            ),
        )

    return ft.View(
        route="/proyectos",
        appbar=barra(page, estado, f"Hola, {estado.usuario['nombre'].split()[0]}"),
        controls=[
            centrar(
                [
                    ft.Text(value="Mis proyectos", size=16, weight=ft.FontWeight.BOLD),
                    lista,
                    ft.Divider(height=18, color=ft.Colors.TRANSPARENT),
                    seccion(
                        "Unirme con un codigo",
                        [
                            codigo,
                            ft.Button(
                                content="Unirme",
                                icon=ft.Icons.GROUP_ADD,
                                on_click=unirse,
                            ),
                        ],
                    ),
                    seccion(
                        "Crear un proyecto nuevo",
                        [
                            nombre,
                            descripcion,
                            ft.Button(
                                content="Crear",
                                icon=ft.Icons.ADD,
                                on_click=crear,
                                bgcolor=ft.Colors.INDIGO_600,
                                color=ft.Colors.WHITE,
                            ),
                        ],
                    ),
                ]
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ------------------------------------------------------- pantalla de tablero


def vista_tablero(page, estado, id_proyecto):
    """Tablero del proyecto con actualizacion en vivo (REQ-03, 04, 05, 09)."""
    estado.id_proyecto = id_proyecto
    usuario = estado.usuario

    try:
        estado.proyecto = proyectos.obtener(id_proyecto, usuario)
    except tareas.ErrorDePermiso:
        avisar(page, "No pertenece a ese proyecto.", ft.Colors.RED_700)
        return None
    if not estado.proyecto:
        avisar(page, "El proyecto ya no existe.", ft.Colors.RED_700)
        return None

    nombre_proyecto = estado.proyecto.get("nombre", "Proyecto")
    codigo_acceso = estado.proyecto.get("codigo_acceso", "")

    lista = ft.Column(spacing=8)
    resumen = ft.Row(spacing=6, wrap=True)
    formulario = ft.Container(visible=False)

    titulo_tarea = ft.TextField(label="Titulo de la tarea", dense=True)
    descripcion_tarea = ft.TextField(
        label="Descripcion (opcional)", multiline=True, min_lines=2, dense=True
    )
    fecha_tarea = ft.TextField(label="Fecha limite", hint_text="AAAA-MM-DD", dense=True)
    responsable_tarea = ft.Dropdown(label="Responsable", options=[], dense=True)

    def integrantes():
        try:
            from datos import repositorio

            return repositorio.listar_integrantes(id_proyecto)
        except Exception:
            return []

    def cargar_responsables():
        responsable_tarea.options = [
            ft.DropdownOption(key=nombre, text=nombre)
            for nombre in sorted(
                {
                    i.get("nombre_usuario") or i.get("nombre") or "Sin nombre"
                    for i in integrantes()
                }
            )
        ]
        if not responsable_tarea.value and responsable_tarea.options:
            responsable_tarea.value = responsable_tarea.options[0].key

    def tarjeta(tarea):
        id_tarea = tarea["SK"].split("#", 1)[1]
        estado_actual = tarea.get("estado", "Pendiente")
        color = COLOR_DE_ESTADO.get(estado_actual, ft.Colors.GREY_700)
        n_comentarios = conteo.get(id_tarea, 0)

        cambios = [
            ft.OutlinedButton(
                content=destino,
                on_click=lambda e, i=id_tarea, d=destino: mover(i, d),
            )
            for destino in ESTADOS
            if destino != estado_actual
        ]

        detalle = [
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Text(
                        value=tarea.get("titulo", "(sin titulo)"),
                        weight=ft.FontWeight.W_600,
                        size=14,
                        expand=True,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                        bgcolor=ft.Colors.with_opacity(0.14, color),
                        border_radius=ft.BorderRadius.all(20),
                        content=ft.Text(value=estado_actual, size=10, color=color),
                    ),
                ],
            ),
            ft.Row(
                spacing=14,
                wrap=True,
                run_spacing=2,
                controls=[
                    ft.Text(
                        value=tarea.get("responsable", "Sin responsable"),
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        value=tarea.get("fecha_limite", ""),
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
            ),
            # Los botones se envuelven en lugar de alinearse a los extremos:
            # en un telefono, dos cambios de estado mas el acceso a los
            # comentarios no caben en una sola linea y antes quedaban fuera de
            # la pantalla, sin forma de alcanzarlos.
            ft.Row(
                wrap=True,
                spacing=6,
                run_spacing=6,
                controls=cambios
                + [
                    ft.TextButton(
                        content=f"Comentarios ({n_comentarios})",
                        icon=ft.Icons.COMMENT,
                        on_click=lambda e, i=id_tarea: page.navigate(
                            f"/tarea/{id_proyecto}/{i}"
                        ),
                    )
                ],
            ),
        ]
        return ft.Card(
            elevation=1,
            content=ft.Container(
                padding=ft.Padding.all(12),
                content=ft.Column(controls=detalle, spacing=8),
            ),
        )

    # Los datos vivos del tablero. Se recargan enteros desde DynamoDB: el
    # aviso de PubSub no transporta el cambio, solo indica que hay que mirar.
    datos = {"tareas": []}
    conteo = {}

    def recargar():
        datos["tareas"] = tareas.listar(id_proyecto, usuario["id_usuario"])
        conteo.clear()
        try:
            conteo.update(comentarios.contar_por_tarea(id_proyecto, usuario["id_usuario"]))
        except Exception:
            pass

    def pintar():
        por_estado = {e: 0 for e in ESTADOS}
        for tarea in datos["tareas"]:
            clave = tarea.get("estado", "Pendiente")
            por_estado[clave] = por_estado.get(clave, 0) + 1

        resumen.controls.clear()
        for etiqueta in ("Todas",) + ESTADOS:
            cantidad = (
                len(datos["tareas"]) if etiqueta == "Todas" else por_estado.get(etiqueta, 0)
            )
            activo = estado.filtro == etiqueta
            color = COLOR_DE_ESTADO.get(etiqueta, SEMILLA)
            resumen.controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                    border_radius=ft.BorderRadius.all(20),
                    bgcolor=color if activo else ft.Colors.with_opacity(0.10, color),
                    border=ft.Border.all(1, ft.Colors.with_opacity(0.35, color)),
                    ink=True,
                    on_click=lambda e, f=etiqueta: filtrar(f),
                    content=ft.Text(
                        value=f"{etiqueta} ({cantidad})",
                        size=11,
                        weight=ft.FontWeight.W_600,
                        color=ft.Colors.WHITE if activo else color,
                    ),
                )
            )

        visibles = [
            t
            for t in datos["tareas"]
            if estado.filtro == "Todas" or t.get("estado") == estado.filtro
        ]
        orden = {e: n for n, e in enumerate(ESTADOS)}
        visibles.sort(
            key=lambda t: (
                orden.get(t.get("estado", "Pendiente"), 9),
                t.get("fecha_limite") or "9999",
            )
        )

        lista.controls.clear()
        if not visibles:
            lista.controls.append(
                ft.Container(
                    padding=ft.Padding.all(24),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        value="No hay tareas en este grupo.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                )
            )
        for tarea in visibles:
            lista.controls.append(tarjeta(tarea))
        page.update()

    def filtrar(etiqueta):
        estado.filtro = etiqueta
        pintar()

    def refrescar_desde_evento():
        """Lo invoca el manejador de PubSub, en otro hilo."""
        try:
            recargar()
            pintar()
        except Exception:
            # Una sesion que ya navego a otra pantalla no debe tumbar el
            # proceso por intentar actualizar controles ausentes.
            pass

    def mover(id_tarea, destino):
        try:
            tareas.cambiar_estado(
                page, id_proyecto, usuario["id_usuario"], id_tarea, destino
            )
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo mover la tarea: {fallo}", ft.Colors.RED_700)
            return
        refrescar_desde_evento()

    def alternar_formulario(e):
        formulario.visible = not formulario.visible
        if formulario.visible:
            cargar_responsables()
        page.update()

    def guardar_tarea(e):
        if not titulo_tarea.value or not titulo_tarea.value.strip():
            avisar(page, "La tarea necesita un titulo.", ft.Colors.RED_700)
            return
        try:
            tareas.crear(
                page,
                id_proyecto,
                usuario["id_usuario"],
                titulo=titulo_tarea.value.strip(),
                descripcion=(descripcion_tarea.value or "").strip(),
                responsable=responsable_tarea.value or usuario["nombre"],
                fecha_limite=(fecha_tarea.value or "").strip(),
            )
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo crear la tarea: {fallo}", ft.Colors.RED_700)
            return
        titulo_tarea.value = ""
        descripcion_tarea.value = ""
        fecha_tarea.value = ""
        formulario.visible = False
        avisar(page, "Tarea creada.", ft.Colors.GREEN_700)
        refrescar_desde_evento()

    formulario.padding = ft.Padding.all(14)
    formulario.border = ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
    formulario.border_radius = ft.BorderRadius.all(12)
    formulario.content = ft.Column(
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[
            ft.Text(value="Nueva tarea", size=13, weight=ft.FontWeight.BOLD),
            titulo_tarea,
            responsable_tarea,
            fecha_tarea,
            descripcion_tarea,
            ft.Row(
                alignment=ft.MainAxisAlignment.END,
                spacing=8,
                controls=[
                    ft.TextButton(content="Cancelar", on_click=alternar_formulario),
                    ft.Button(
                        content="Guardar",
                        icon=ft.Icons.CHECK,
                        on_click=guardar_tarea,
                        bgcolor=ft.Colors.INDIGO_600,
                        color=ft.Colors.WHITE,
                    ),
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
        avisar(page, f"No se pudieron cargar las tareas: {fallo}", ft.Colors.RED_700)

    encabezado = ft.Container(
        padding=ft.Padding.symmetric(horizontal=4),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            wrap=True,
            run_spacing=10,
            controls=[
                ft.Column(
                    spacing=0,
                    controls=[
                        ft.Text(
                            value="Codigo para invitar",
                            size=10,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Text(
                            value=codigo_acceso,
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            selectable=True,
                        ),
                    ],
                ),
                ft.Button(
                    content="Nueva tarea",
                    icon=ft.Icons.ADD,
                    on_click=alternar_formulario,
                    bgcolor=ft.Colors.INDIGO_600,
                    color=ft.Colors.WHITE,
                ),
            ],
        ),
    )

    vista = ft.View(
        route=f"/tablero/{id_proyecto}",
        appbar=barra(
            page, estado, nombre_proyecto, con_volver=True, ruta_de_regreso="/proyectos"
        ),
        controls=[centrar([encabezado, formulario, resumen, lista])],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )
    # Se puebla el contenido antes de devolver la vista, para que el enrutador
    # la entregue ya pintada y no haya un parpadeo con el tablero vacio.
    _pintar_inicial(datos, conteo, estado, resumen, lista, tarjeta)
    return vista


def _pintar_inicial(datos, conteo, estado, resumen, lista, tarjeta):
    """Primer pintado, sin tocar la pagina todavia.

    Se separa de pintar() porque pintar() termina con page.update(), y en el
    momento de construir la vista esta aun no esta montada en la pagina.
    """
    por_estado = {e: 0 for e in ESTADOS}
    for tarea in datos["tareas"]:
        clave = tarea.get("estado", "Pendiente")
        por_estado[clave] = por_estado.get(clave, 0) + 1
    resumen.controls.clear()
    for etiqueta in ("Todas",) + ESTADOS:
        cantidad = (
            len(datos["tareas"]) if etiqueta == "Todas" else por_estado.get(etiqueta, 0)
        )
        activo = estado.filtro == etiqueta
        color = COLOR_DE_ESTADO.get(etiqueta, SEMILLA)
        resumen.controls.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                border_radius=ft.BorderRadius.all(20),
                bgcolor=color if activo else ft.Colors.with_opacity(0.10, color),
                border=ft.Border.all(1, ft.Colors.with_opacity(0.35, color)),
                content=ft.Text(
                    value=f"{etiqueta} ({cantidad})",
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.WHITE if activo else color,
                ),
            )
        )
    lista.controls.clear()
    visibles = [
        t
        for t in datos["tareas"]
        if estado.filtro == "Todas" or t.get("estado") == estado.filtro
    ]
    orden = {e: n for n, e in enumerate(ESTADOS)}
    visibles.sort(
        key=lambda t: (
            orden.get(t.get("estado", "Pendiente"), 9),
            t.get("fecha_limite") or "9999",
        )
    )
    if not visibles:
        lista.controls.append(
            ft.Container(
                padding=ft.Padding.all(24),
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    value="Todavia no hay tareas. Cree la primera.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            )
        )
    for tarea in visibles:
        lista.controls.append(tarjeta(tarea))


# --------------------------------------------- pantalla de detalle de tarea


def vista_tarea(page, estado, id_proyecto, id_tarea):
    """Detalle de una tarea con su hilo de comentarios (REQ-06)."""
    usuario = estado.usuario
    try:
        proyectos.obtener(id_proyecto, usuario)
    except tareas.ErrorDePermiso:
        avisar(page, "No pertenece a ese proyecto.", ft.Colors.RED_700)
        return None

    la_tarea = None
    try:
        for t in tareas.listar(id_proyecto, usuario["id_usuario"]):
            if t["SK"].split("#", 1)[1] == id_tarea:
                la_tarea = t
                break
    except Exception as fallo:
        avisar(page, f"No se pudo cargar la tarea: {fallo}", ft.Colors.RED_700)

    hilo = ft.Column(spacing=8)
    texto = ft.TextField(
        label="Escriba un comentario",
        multiline=True,
        min_lines=2,
        max_lines=5,
        dense=True,
    )

    def burbuja(comentario):
        mio = comentario.get("id_autor") == usuario["id_usuario"]
        return ft.Container(
            padding=ft.Padding.all(10),
            bgcolor=ft.Colors.with_opacity(
                0.10, SEMILLA if mio else ft.Colors.BLUE_GREY
            ),
            border_radius=ft.BorderRadius.all(10),
            content=ft.Column(
                spacing=3,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text(
                                value=comentario.get("nombre_autor", "Alguien"),
                                size=11,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                value=(comentario.get("fecha", "") or "")[:19].replace(
                                    "T", " "
                                ),
                                size=10,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                    ),
                    ft.Text(value=comentario.get("texto", ""), size=13, selectable=True),
                ],
            ),
        )

    def construir_hilo():
        hilo.controls.clear()
        try:
            historial = comentarios.listar(id_proyecto, usuario["id_usuario"], id_tarea)
        except Exception as fallo:
            hilo.controls.append(ft.Text(value=f"No se pudo cargar el hilo: {fallo}", size=12))
            return
        if not historial:
            hilo.controls.append(
                ft.Text(
                    value="Nadie ha comentado todavia.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )
        for comentario in historial:
            hilo.controls.append(burbuja(comentario))

    def refrescar_hilo():
        try:
            construir_hilo()
            page.update()
        except Exception:
            pass

    def enviar(e):
        if not texto.value or not texto.value.strip():
            return
        try:
            comentarios.agregar(
                page,
                id_proyecto,
                usuario["id_usuario"],
                usuario["nombre"],
                id_tarea,
                texto.value,
            )
        except tareas.ErrorDePermiso as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except ValueError as fallo:
            avisar(page, str(fallo), ft.Colors.RED_700)
            return
        except Exception as fallo:
            avisar(page, f"No se pudo publicar: {fallo}", ft.Colors.RED_700)
            return
        texto.value = ""
        refrescar_hilo()

    construir_hilo()

    # El detalle tambien escucha el tema del proyecto: si un companero comenta
    # la misma tarea mientras esta pantalla esta abierta, el hilo se actualiza.
    soltar_suscripcion(page, estado)
    tareas.suscribir(page, id_proyecto, lambda mensaje: refrescar_hilo())
    estado.suscrito_a = id_proyecto
    estado.refrescar = refrescar_hilo

    estado_actual = (la_tarea or {}).get("estado", "Pendiente")
    color = COLOR_DE_ESTADO.get(estado_actual, ft.Colors.GREY_700)
    ficha = ft.Container(
        padding=ft.Padding.all(14),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=ft.BorderRadius.all(12),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text(
                    value=(la_tarea or {}).get("titulo", "Tarea"),
                    size=16,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Row(
                    spacing=10,
                    wrap=True,
                    controls=[
                        ft.Container(
                            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                            bgcolor=ft.Colors.with_opacity(0.14, color),
                            border_radius=ft.BorderRadius.all(20),
                            content=ft.Text(value=estado_actual, size=10, color=color),
                        ),
                        ft.Text(
                            value=(la_tarea or {}).get("responsable", ""),
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Text(
                            value=(la_tarea or {}).get("fecha_limite", ""),
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                ),
                ft.Text(
                    value=(la_tarea or {}).get("descripcion", "") or "Sin descripcion.",
                    size=12,
                    selectable=True,
                ),
            ],
        ),
    )

    return ft.View(
        route=f"/tarea/{id_proyecto}/{id_tarea}",
        appbar=barra(
            page,
            estado,
            "Detalle de la tarea",
            con_volver=True,
            ruta_de_regreso=f"/tablero/{id_proyecto}",
        ),
        controls=[
            centrar(
                [
                    ficha,
                    ft.Text(value="Comentarios", size=13, weight=ft.FontWeight.BOLD),
                    hilo,
                    texto,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.END,
                        controls=[
                            ft.Button(
                                content="Publicar",
                                icon=ft.Icons.SEND,
                                on_click=enviar,
                                bgcolor=ft.Colors.INDIGO_600,
                                color=ft.Colors.WHITE,
                            )
                        ],
                    ),
                ]
            )
        ],
        padding=ft.Padding.all(0),
        scroll=ft.ScrollMode.AUTO,
    )


# ----------------------------------------------------------------- enrutador


def main(page: ft.Page):
    estado = Estado()

    page.title = "SyncTask CUN"
    page.theme = ft.Theme(color_scheme_seed=SEMILLA)
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
            vista = (
                vista_proyectos(page, estado)
                if estado.usuario
                else vista_ingreso(page, estado)
            )

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