"""Gestion de tareas con propagacion entre sesiones (REQ-03, 04, 05, 07, 09).

Este modulo es la capa donde se encuentran la persistencia y el tiempo real.
Dos reglas gobiernan todas sus operaciones de escritura:

1. Pertenencia antes de escribir. Cada operacion comprueba que el usuario
   pertenezca al proyecto antes de tocar la base de datos (REQ-07). La
   comprobacion vive aqui y no en la interfaz, porque la interfaz puede
   evadirse y esta capa no.

2. Persistir primero, difundir despues. El mensaje de publicacion se emite
   unicamente cuando DynamoDB confirma la escritura, de modo que ninguna
   sesion muestre un estado que la base de datos no haya aceptado. Si la
   escritura falla, no se difunde nada y la excepcion sube a la interfaz.

La difusion usa un tema por proyecto. Emitir a todas las sesiones seria mas
simple, pero entregaria titulos de tareas de un proyecto a integrantes de
otro, que es exactamente lo que REQ-07 prohibe.
"""

from datos import repositorio
from datos.repositorio import ESTADOS

# Tipos de evento que viajan en el mensaje de publicacion.
TAREA_CREADA = "tarea_creada"
ESTADO_CAMBIADO = "estado_cambiado"


class ErrorDePermiso(Exception):
    """El usuario no pertenece al proyecto sobre el que intenta operar."""


def tema_de(id_proyecto):
    """Nombre del tema de publicacion asociado a un proyecto."""
    return f"proyecto:{id_proyecto}"


def _exigir_pertenencia(id_proyecto, id_usuario):
    """Interrumpe la operacion si el usuario no es integrante (REQ-07)."""
    if not repositorio.es_miembro(id_proyecto, id_usuario):
        raise ErrorDePermiso(
            "No pertenece a este proyecto, de modo que no puede consultarlo "
            "ni modificarlo."
        )


# ------------------------------------------------------------ suscripcion

def suscribir(page, id_proyecto, manejador):
    """Suscribe la sesion actual a los cambios de un proyecto (REQ-05).

    El manejador recibe el mensaje difundido y es responsable de actualizar
    los controles y de llamar a page.update(). Se invoca en la sesion de
    cada integrante que tenga el tablero abierto.
    """
    def _envoltura(tema, mensaje):
        manejador(mensaje)

    page.pubsub.subscribe_topic(tema_de(id_proyecto), _envoltura)


def _difundir(page, id_proyecto, mensaje):
    """Emite el mensaje a todas las sesiones suscritas al proyecto."""
    page.pubsub.send_all_on_topic(tema_de(id_proyecto), mensaje)


# ----------------------------------------------------------------- tareas

def crear(page, id_proyecto, id_usuario, titulo, descripcion,
          responsable, fecha_limite):
    """Crea una tarea y la difunde a las sesiones del proyecto (REQ-03).

    Devuelve el elemento persistido. Lanza ErrorDePermiso si el usuario no
    pertenece al proyecto y ValueError si el titulo esta vacio.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    if not titulo or not titulo.strip():
        raise ValueError("La tarea debe tener un titulo.")

    tarea = repositorio.crear_tarea(
        id_proyecto=id_proyecto,
        titulo=titulo.strip(),
        descripcion=(descripcion or "").strip(),
        responsable=responsable,
        fecha_limite=fecha_limite,
    )

    _difundir(page, id_proyecto, {
        "evento": TAREA_CREADA,
        "tarea": tarea,
        "autor": id_usuario,
    })
    return tarea


def cambiar_estado(page, id_proyecto, id_usuario, id_tarea, nuevo_estado):
    """Cambia el estado de una tarea y lo refleja en las demas sesiones.

    Cubre REQ-04 (persistencia del nuevo estado con su fecha) y REQ-05
    (propagacion sin recargar). La validacion del estado la hace el
    repositorio, que es donde vive la lista de estados admitidos.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    tarea = repositorio.cambiar_estado(id_proyecto, id_tarea, nuevo_estado)

    _difundir(page, id_proyecto, {
        "evento": ESTADO_CAMBIADO,
        "tarea": tarea,
        "autor": id_usuario,
    })
    return tarea


def listar(id_proyecto, id_usuario):
    """Devuelve las tareas del proyecto si el usuario pertenece a el."""
    _exigir_pertenencia(id_proyecto, id_usuario)
    return repositorio.listar_tareas(id_proyecto)


def agrupar_por_estado(tareas):
    """Organiza las tareas en las tres columnas del tablero."""
    return {estado: [t for t in tareas if t.get("estado") == estado]
            for estado in ESTADOS}


def resumen(id_proyecto, id_usuario):
    """Conteo de tareas por estado y por responsable (REQ-09).

    Devuelve un diccionario con dos entradas: 'por_estado', con los tres
    estados siempre presentes aunque esten en cero, y 'por_responsable',
    con el desglose de cada integrante. La coherencia con el tablero esta
    garantizada porque ambos se calculan sobre la misma consulta.
    """
    tareas = listar(id_proyecto, id_usuario)

    por_estado = {estado: 0 for estado in ESTADOS}
    por_responsable = {}

    for tarea in tareas:
        estado = tarea.get("estado")
        if estado in por_estado:
            por_estado[estado] += 1

        responsable = tarea.get("responsable") or "Sin asignar"
        casillas = por_responsable.setdefault(
            responsable, {estado: 0 for estado in ESTADOS}
        )
        if estado in casillas:
            casillas[estado] += 1

    return {
        "total": len(tareas),
        "por_estado": por_estado,
        "por_responsable": por_responsable,
    }