"""Verificacion de la capa de logica contra DynamoDB (OE-3).

Ejercita los requisitos de prioridad alta que no dependen de la interfaz:
REQ-01, REQ-02, REQ-03, REQ-04, REQ-07, REQ-08, REQ-09 y REQ-11. La
difusion (REQ-05) se comprueba aqui solo en su parte logica —que el
mensaje se emita despues de la escritura y con el contenido correcto—
mediante un doble de prueba del mecanismo de publicacion; su
comportamiento real entre sesiones se verifico con pubsub_demo.py.

Uso, desde la raiz del proyecto:
    cd ~/synctask && source ~/venv/bin/activate && python3 prueba_tareas.py

El script escribe datos reales en la tabla SyncTaskCUN. Los identificadores
llevan una marca de tiempo, de modo que cada ejecucion crea su propio juego
de datos y no interfiere con las anteriores.
"""

import traceback
from datetime import datetime

from datos import repositorio
from modulos import sesion, tareas

MARCA = datetime.now().strftime("%Y%m%d%H%M%S")

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    """Registra el resultado de una comprobacion y lo imprime."""
    _resultados.append(bool(condicion))
    marca = "  OK  " if condicion else " FALLA"
    print(f"[{marca}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


def espera_error(descripcion, excepcion, funcion, *args, **kwargs):
    """Comprueba que una operacion prohibida efectivamente se rechace."""
    try:
        funcion(*args, **kwargs)
    except excepcion as error:
        comprobar(descripcion, True, f"rechazado: {error}")
        return
    except Exception as error:  # noqa: BLE001
        comprobar(descripcion, False, f"excepcion inesperada: {error!r}")
        return
    comprobar(descripcion, False, "la operacion se permitio y no debia")


class PubSubFalso:
    """Doble de prueba del mecanismo de publicacion de Flet.

    Guarda los mensajes emitidos para poder afirmar que la difusion ocurre
    despues de la escritura y con el contenido esperado, sin necesidad de
    levantar la aplicacion ni abrir un navegador.
    """

    def __init__(self):
        self.mensajes = []

    def send_all_on_topic(self, tema, mensaje):
        self.mensajes.append((tema, mensaje))

    def subscribe_topic(self, tema, manejador):
        pass

    @property
    def ultimo(self):
        return self.mensajes[-1][1] if self.mensajes else None


class PaginaFalsa:
    def __init__(self):
        self.pubsub = PubSubFalso()


def main():
    page = PaginaFalsa()
    print(f"\nVerificacion de la capa de logica - ejecucion {MARCA}")
    print("=" * 64)

    # ---------------------------------------------- registro y credenciales
    print("\nRegistro y autenticacion (REQ-08, REQ-11)")

    correo_a = f"ana.{MARCA}@cun.edu.co"
    correo_b = f"bruno.{MARCA}@cun.edu.co"
    correo_c = f"carla.{MARCA}@cun.edu.co"
    clave = "ClaveDePrueba123"

    espera_error(
        "REQ-08: el registro sin autorizacion del titular se rechaza",
        sesion.ErrorDeRegistro,
        sesion.registrar_usuario,
        "Ana Prueba", correo_a, clave, False,
    )

    usuario_a = sesion.registrar_usuario("Ana Prueba", correo_a, clave, True)
    comprobar(
        "REQ-08: el registro con autorizacion crea la cuenta",
        usuario_a and usuario_a.get("id_usuario"),
        f"id: {usuario_a['id_usuario']}",
    )

    comprobar(
        "REQ-11: el perfil devuelto no expone la contraseña ni la sal",
        "clave_derivada" not in usuario_a and "sal" not in usuario_a,
    )

    espera_error(
        "El correo duplicado se rechaza",
        sesion.ErrorDeRegistro,
        sesion.registrar_usuario,
        "Ana Repetida", correo_a, clave, True,
    )

    comprobar(
        "La autenticacion con credenciales correctas devuelve el perfil",
        sesion.autenticar(correo_a, clave) is not None,
    )
    comprobar(
        "La autenticacion con contraseña incorrecta devuelve None",
        sesion.autenticar(correo_a, "OtraClave999") is None,
    )
    comprobar(
        "La autenticacion de un correo inexistente devuelve None",
        sesion.autenticar(f"nadie.{MARCA}@cun.edu.co", clave) is None,
    )

    usuario_b = sesion.registrar_usuario("Bruno Prueba", correo_b, clave, True)
    usuario_c = sesion.registrar_usuario("Carla Ajena", correo_c, clave, True)

    # ------------------------------------------------------------ proyecto
    print("\nProyectos e integrantes (REQ-01, REQ-02)")

    proyecto = repositorio.crear_proyecto(
        nombre="Proyecto de verificacion",
        descripcion="Creado por prueba_tareas.py",
        id_administrador=usuario_a["id_usuario"],
        nombre_administrador=usuario_a["nombre"],
    )
    id_proyecto = proyecto["PK"].split("#", 1)[1]
    codigo = proyecto["codigo_acceso"]

    comprobar(
        "REQ-01: el proyecto se crea con un codigo de seis caracteres",
        len(codigo) == 6,
        f"codigo: {codigo}",
    )

    comprobar(
        "El administrador queda registrado como integrante del proyecto",
        repositorio.es_miembro(id_proyecto, usuario_a["id_usuario"]),
        "sin esto, el creador no podria escribir en su propio proyecto",
    )

    comprobar(
        "REQ-02: el codigo de acceso resuelve al proyecto",
        (repositorio.buscar_proyecto_por_codigo(codigo) or {}).get("PK")
        == proyecto["PK"],
    )

    repositorio.unirse_a_proyecto(codigo, usuario_b["id_usuario"], usuario_b["nombre"])
    comprobar(
        "REQ-02: un integrante se vincula con el codigo",
        repositorio.es_miembro(id_proyecto, usuario_b["id_usuario"]),
    )

    comprobar(
        "Un usuario ajeno no figura como integrante",
        not repositorio.es_miembro(id_proyecto, usuario_c["id_usuario"]),
    )

    comprobar(
        "El proyecto reporta dos integrantes",
        len(repositorio.listar_integrantes(id_proyecto)) == 2,
    )

    # --------------------------------------------------------------- tareas
    print("\nTareas, permisos y difusion (REQ-03, REQ-04, REQ-05, REQ-07)")

    espera_error(
        "REQ-07: un usuario ajeno no puede crear tareas en el proyecto",
        tareas.ErrorDePermiso,
        tareas.crear,
        page, id_proyecto, usuario_c["id_usuario"],
        "Tarea intrusa", "", usuario_c["nombre"], "2026-12-01",
    )

    espera_error(
        "REQ-07: un usuario ajeno no puede listar las tareas del proyecto",
        tareas.ErrorDePermiso,
        tareas.listar,
        id_proyecto, usuario_c["id_usuario"],
    )

    mensajes_antes = len(page.pubsub.mensajes)
    tarea = tareas.crear(
        page, id_proyecto, usuario_a["id_usuario"],
        titulo="Aplicar encuesta diagnostica",
        descripcion="Instrumento de la Semana 2",
        responsable=usuario_b["nombre"],
        fecha_limite="2026-10-15",
    )
    id_tarea = tarea["SK"].split("#", 1)[1]

    comprobar(
        "REQ-03: la tarea se crea en estado Pendiente",
        tarea["estado"] == "Pendiente",
    )
    comprobar(
        "REQ-05: la creacion emite un mensaje de difusion",
        len(page.pubsub.mensajes) == mensajes_antes + 1
        and page.pubsub.ultimo["evento"] == tareas.TAREA_CREADA,
    )
    comprobar(
        "El mensaje viaja en el tema del proyecto",
        page.pubsub.mensajes[-1][0] == tareas.tema_de(id_proyecto),
        f"tema: {page.pubsub.mensajes[-1][0]}",
    )

    espera_error(
        "Una tarea sin titulo se rechaza",
        ValueError,
        tareas.crear,
        page, id_proyecto, usuario_a["id_usuario"],
        "   ", "", usuario_b["nombre"], "2026-10-15",
    )

    actualizada = tareas.cambiar_estado(
        page, id_proyecto, usuario_b["id_usuario"], id_tarea, "En progreso"
    )
    comprobar(
        "REQ-04: el estado cambia y queda persistido",
        actualizada["estado"] == "En progreso",
    )
    comprobar(
        "REQ-04: el cambio registra su fecha de actualizacion",
        actualizada["fecha_actualizacion"] != actualizada["fecha_creacion"],
    )
    comprobar(
        "REQ-05: el cambio de estado emite su mensaje de difusion",
        page.pubsub.ultimo["evento"] == tareas.ESTADO_CAMBIADO,
    )

    espera_error(
        "Un estado fuera de los tres admitidos se rechaza",
        ValueError,
        tareas.cambiar_estado,
        page, id_proyecto, usuario_b["id_usuario"], id_tarea, "Archivado",
    )

    espera_error(
        "REQ-07: un usuario ajeno no puede cambiar el estado de una tarea",
        tareas.ErrorDePermiso,
        tareas.cambiar_estado,
        page, id_proyecto, usuario_c["id_usuario"], id_tarea, "Terminado",
    )

    # -------------------------------------------------------------- resumen
    print("\nVista de resumen (REQ-09)")

    tareas.crear(
        page, id_proyecto, usuario_a["id_usuario"],
        titulo="Redactar aviso de privacidad",
        descripcion="", responsable=usuario_a["nombre"], fecha_limite="2026-10-20",
    )

    listado = tareas.listar(id_proyecto, usuario_a["id_usuario"])
    comprobar(
        "La consulta de tareas devuelve solo tareas del proyecto",
        len(listado) == 2 and all(t["SK"].startswith("TAREA#") for t in listado),
        f"tareas recuperadas: {len(listado)}",
    )

    datos = tareas.resumen(id_proyecto, usuario_a["id_usuario"])
    comprobar(
        "REQ-09: el resumen cuenta el total correctamente",
        datos["total"] == 2,
    )
    comprobar(
        "REQ-09: el resumen desglosa por estado",
        datos["por_estado"]["Pendiente"] == 1
        and datos["por_estado"]["En progreso"] == 1,
        f"por estado: {datos['por_estado']}",
    )
    comprobar(
        "REQ-09: el resumen desglosa por responsable",
        len(datos["por_responsable"]) == 2,
        f"responsables: {list(datos['por_responsable'])}",
    )

    # --------------------------------------------------------------- cierre
    print("\n" + "=" * 64)
    total = len(_resultados)
    exitosas = sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    print(f"Proyecto de prueba: {id_proyecto}")
    print(f"Codigo de acceso:   {codigo}")
    if exitosas == total:
        print("\nToda la capa de logica responde segun lo esperado.")
    else:
        print(f"\nHay {total - exitosas} comprobacion(es) sin superar.")
    return 0 if exitosas == total else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        print("\nLa verificacion se interrumpio por un error no controlado:\n")
        traceback.print_exc()
        raise SystemExit(2)