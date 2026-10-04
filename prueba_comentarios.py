"""Verificacion del modulo de comentarios contra DynamoDB (REQ-06, REQ-07).

Complementa a prueba_tareas.py y se ejecuta igual, desde la raiz del proyecto:

    cd ~/synctask && source ~/venv/bin/activate && python3 prueba_comentarios.py

Incluye deliberadamente una comprobacion de regresion —la numero 8— que
verifica que los comentarios no aparecen en la consulta de tareas. Ese era el
defecto del modelo de datos de la Entrega 1 (hallazgo AUT-02): si alguien
revirtiera el prefijo, esta prueba lo detectaria.
"""

import traceback
from datetime import datetime

from datos import repositorio
from modulos import comentarios, sesion, tareas

MARCA = datetime.now().strftime("%Y%m%d%H%M%S")

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    marca = "  OK  " if condicion else " FALLA"
    print(f"[{marca}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


def espera_error(descripcion, excepcion, funcion, *args, **kwargs):
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
    """Doble de prueba del mecanismo de publicacion de Flet."""

    def __init__(self):
        self.mensajes = []

    def send_all_on_topic(self, tema, mensaje):
        self.mensajes.append((tema, mensaje))

    def subscribe_topic(self, tema, manejador):
        pass

    def unsubscribe_topic(self, tema):
        pass

    @property
    def ultimo(self):
        return self.mensajes[-1][1] if self.mensajes else None


class PaginaFalsa:
    def __init__(self):
        self.pubsub = PubSubFalso()


def main():
    page = PaginaFalsa()
    print(f"\nVerificacion del modulo de comentarios - ejecucion {MARCA}")
    print("=" * 64)

    # ------------------------------------------------------- datos de partida
    clave = "ClaveDePrueba123"
    autor = sesion.registrar_usuario(
        "Ana Comenta", f"ana.c.{MARCA}@cun.edu.co", clave, True)
    companero = sesion.registrar_usuario(
        "Bruno Comenta", f"bruno.c.{MARCA}@cun.edu.co", clave, True)
    ajeno = sesion.registrar_usuario(
        "Carla Ajena", f"carla.c.{MARCA}@cun.edu.co", clave, True)

    proyecto = repositorio.crear_proyecto(
        nombre="Proyecto de comentarios",
        descripcion="Creado por prueba_comentarios.py",
        id_administrador=autor["id_usuario"],
        nombre_administrador=autor["nombre"],
    )
    id_proyecto = proyecto["PK"].split("#", 1)[1]
    repositorio.unirse_a_proyecto(
        proyecto["codigo_acceso"], companero["id_usuario"], companero["nombre"])

    tarea_a = tareas.crear(
        page, id_proyecto, autor["id_usuario"],
        titulo="Redactar el marco teorico", descripcion="",
        responsable=companero["nombre"], fecha_limite="2026-10-20")
    tarea_b = tareas.crear(
        page, id_proyecto, autor["id_usuario"],
        titulo="Preparar la sustentacion", descripcion="",
        responsable=autor["nombre"], fecha_limite="2026-10-25")
    id_tarea_a = tarea_a["SK"].split("#", 1)[1]
    id_tarea_b = tarea_b["SK"].split("#", 1)[1]

    print("\nControl de acceso (REQ-07)")

    espera_error(
        "Un usuario ajeno no puede comentar en una tarea del proyecto",
        tareas.ErrorDePermiso,
        comentarios.agregar,
        page, id_proyecto, ajeno["id_usuario"], ajeno["nombre"],
        id_tarea_a, "Comentario intruso",
    )
    espera_error(
        "Un usuario ajeno no puede leer el historial de una tarea",
        tareas.ErrorDePermiso,
        comentarios.listar,
        id_proyecto, ajeno["id_usuario"], id_tarea_a,
    )
    espera_error(
        "Un comentario vacio se rechaza",
        ValueError,
        comentarios.agregar,
        page, id_proyecto, autor["id_usuario"], autor["nombre"],
        id_tarea_a, "   ",
    )

    print("\nRegistro y difusion del comentario (REQ-06, REQ-05)")

    mensajes_antes = len(page.pubsub.mensajes)
    primero = comentarios.agregar(
        page, id_proyecto, autor["id_usuario"], autor["nombre"],
        id_tarea_a, "Ya tengo las fuentes; subo el borrador manana.")

    comprobar(
        "REQ-06: el comentario conserva autor, identificador del autor y fecha",
        primero.get("nombre_autor") == autor["nombre"]
        and primero.get("id_autor") == autor["id_usuario"]
        and primero.get("fecha"),
        f"autor: {primero.get('nombre_autor')} · fecha: {primero.get('fecha')}",
    )
    comprobar(
        "REQ-05: el comentario emite un mensaje de difusion en el tema del proyecto",
        len(page.pubsub.mensajes) == mensajes_antes + 1
        and page.pubsub.ultimo["evento"] == comentarios.COMENTARIO_AGREGADO
        and page.pubsub.mensajes[-1][0] == tareas.tema_de(id_proyecto),
        f"tema: {page.pubsub.mensajes[-1][0]}",
    )

    segundo = comentarios.agregar(
        page, id_proyecto, companero["id_usuario"], companero["nombre"],
        id_tarea_a, "Perfecto, yo reviso la bibliografia.")
    comentarios.agregar(
        page, id_proyecto, autor["id_usuario"], autor["nombre"],
        id_tarea_b, "Comentario de otra tarea, no debe mezclarse.")

    print("\nHistorial y aislamiento entre tareas (REQ-06)")

    historial = comentarios.listar(id_proyecto, autor["id_usuario"], id_tarea_a)
    comprobar(
        "REQ-06: el historial de la tarea devuelve sus dos comentarios",
        len(historial) == 2,
        f"comentarios recuperados: {len(historial)}",
    )
    comprobar(
        "REQ-06: el historial llega en orden cronologico sin ordenar en memoria",
        len(historial) == 2
        and historial[0]["texto"] == primero["texto"]
        and historial[1]["texto"] == segundo["texto"],
    )
    comprobar(
        "Los comentarios de una tarea no aparecen en el historial de otra",
        all(c["id_tarea"] == id_tarea_a for c in historial),
    )

    print("\nRegresion del modelo de datos (hallazgo AUT-02)")

    listado = tareas.listar(id_proyecto, autor["id_usuario"])
    comprobar(
        "La consulta de tareas devuelve solo tareas, sin comentarios mezclados",
        len(listado) == 2 and all(t["SK"].startswith("TAREA#") for t in listado),
        f"elementos devueltos: {len(listado)} · claves: "
        f"{[t['SK'].split('#')[0] for t in listado]}",
    )

    conteo = comentarios.contar_por_tarea(id_proyecto, autor["id_usuario"])
    comprobar(
        "El conteo por tarea es coherente con el historial",
        conteo.get(id_tarea_a) == 2 and conteo.get(id_tarea_b) == 1,
        f"conteo: {conteo}",
    )

    print("\n" + "=" * 64)
    total = len(_resultados)
    exitosas = sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    print(f"Proyecto de prueba: {id_proyecto}")
    if exitosas == total:
        print("\nEl modulo de comentarios responde segun lo esperado.")
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