"""Comentarios por tarea con autor y marca de tiempo (REQ-06).

Sigue las mismas dos reglas que el modulo de tareas, de las que depende la
coherencia del sistema:

1. Pertenencia antes de leer o escribir. Se reutiliza la validacion de
   modulos.tareas para que exista una sola implementacion del control de
   acceso (REQ-07): si se duplicara, una de las dos copias quedaria
   desactualizada tarde o temprano.

2. Persistir primero y difundir despues. El mensaje de publicacion se emite
   solo cuando DynamoDB confirma la escritura, de modo que ninguna sesion
   muestre un comentario que la base de datos no haya aceptado.

El comentario guarda el identificador del autor ademas de su nombre visible.
Esa separacion es la que permite atender el derecho de supresion declarado en
la Tabla 3 de la Entrega 1: al eliminar una cuenta se sustituye el nombre por
una etiqueta generica y el hilo de la discusion se conserva, sin retener datos
personales del titular.
"""

from datos import repositorio
from modulos.tareas import _exigir_pertenencia, _difundir

COMENTARIO_AGREGADO = "comentario_agregado"


def agregar(page, id_proyecto, id_usuario, nombre_usuario, id_tarea, texto):
    """Publica un comentario y lo difunde a las sesiones del proyecto (REQ-06).

    Devuelve el comentario persistido. Lanza ErrorDePermiso si el usuario no
    pertenece al proyecto y ValueError si el texto esta vacio.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    comentario = repositorio.agregar_comentario(
        id_proyecto=id_proyecto,
        id_tarea=id_tarea,
        id_autor=id_usuario,
        nombre_autor=nombre_usuario,
        texto=texto,
    )

    _difundir(page, id_proyecto, {
        "evento": COMENTARIO_AGREGADO,
        "comentario": comentario,
        "autor": id_usuario,
    })
    return comentario


def listar(id_proyecto, id_usuario, id_tarea):
    """Devuelve el historial de una tarea en orden cronologico (REQ-06).

    El orden lo garantiza la clave de ordenamiento, que incluye la marca de
    tiempo: no se ordena en memoria.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)
    return repositorio.listar_comentarios(id_proyecto, id_tarea)


def contar_por_tarea(id_proyecto, id_usuario):
    """Numero de comentarios de cada tarea del proyecto.

    Permite que el tablero muestre un indicador en la tarjeta sin consultar
    una vez por tarea. Recupera todos los comentarios del proyecto con una
    sola consulta y los agrupa en memoria.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    conteo = {}
    for comentario in repositorio.listar_comentarios_del_proyecto(id_proyecto):
        id_tarea = comentario.get("id_tarea")
        conteo[id_tarea] = conteo.get(id_tarea, 0) + 1
    return conteo