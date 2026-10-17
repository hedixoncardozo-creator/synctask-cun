"""Comentarios por tarea, con autor, marca de tiempo, edicion y supresion.

Reemplaza por completo al archivo anterior (REQ-06, REQ-07).

Sigue las mismas dos reglas que el modulo de tareas, de las que depende la
coherencia del sistema:

1. Pertenencia antes de leer o escribir. Se reutiliza la validacion de
   modulos.tareas para que exista una sola implementacion del control de
   acceso (REQ-07): si se duplicara, una de las dos copias quedaria
   desactualizada tarde o temprano.

2. Persistir primero y difundir despues. El mensaje de publicacion se emite
   solo cuando DynamoDB confirma la escritura, de modo que ninguna sesion
   muestre un comentario que la base de datos no haya aceptado.

Sobre la edicion y la supresion, que son nuevas: pertenecer al proyecto
autoriza a comentar, pero no a tocar lo que escribio otro. Esa segunda
comprobacion —la autoria— vive en la capa de datos, expresada como condicion
de la escritura, y no aqui. La razon es que una comprobacion en Python solo
detiene a quien pasa por Python; una condicion en DynamoDB no tiene camino
alternativo. Este modulo se limita a traducir el rechazo en un mensaje
comprensible.

El comentario guarda el identificador del autor ademas de su nombre visible.
Esa separacion es la que permite atender el derecho de supresion declarado en
la Tabla 3 de la Entrega 1: al eliminar una cuenta se sustituye el nombre por
una etiqueta generica y el hilo de la discusion se conserva, sin retener datos
personales del titular.
"""

from datos import repositorio
from datos.repositorio import ErrorDeAutoria  # noqa: F401  (se reexporta)
from modulos.tareas import _difundir, _exigir_pertenencia

COMENTARIO_AGREGADO = "comentario_agregado"
COMENTARIO_EDITADO = "comentario_editado"
COMENTARIO_ELIMINADO = "comentario_eliminado"

LEYENDA_ELIMINADO = "Este comentario fue eliminado por su autor."


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


def editar(page, id_proyecto, id_usuario, clave_orden, texto):
    """Cambia el texto de un comentario propio y avisa a los demas.

    `clave_orden` es la clave de ordenamiento del elemento, que es lo que
    identifica a un comentario concreto dentro del proyecto.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    comentario = repositorio.editar_comentario(
        id_proyecto=id_proyecto,
        clave_orden=clave_orden,
        id_autor=id_usuario,
        texto=texto,
    )

    _difundir(page, id_proyecto, {
        "evento": COMENTARIO_EDITADO,
        "comentario": comentario,
        "autor": id_usuario,
    })
    return comentario


def eliminar(page, id_proyecto, id_usuario, clave_orden):
    """Suprime el contenido de un comentario propio, conservando la huella."""
    _exigir_pertenencia(id_proyecto, id_usuario)

    comentario = repositorio.eliminar_comentario(
        id_proyecto=id_proyecto,
        clave_orden=clave_orden,
        id_autor=id_usuario,
    )

    _difundir(page, id_proyecto, {
        "evento": COMENTARIO_ELIMINADO,
        "comentario": comentario,
        "autor": id_usuario,
    })
    return comentario


def esta_eliminado(comentario):
    """Un comentario suprimido se reconoce por su marca, no por el texto vacio."""
    return bool(comentario.get("eliminado"))


def fue_editado(comentario):
    return bool(comentario.get("fecha_edicion"))


def listar(id_proyecto, id_usuario, id_tarea):
    """Devuelve el historial de una tarea en orden cronologico (REQ-06).

    Incluye los comentarios suprimidos, marcados como tales. Ocultarlos
    dejaria huecos inexplicables en una conversacion que otros ya leyeron.
    El orden lo garantiza la clave de ordenamiento, que incluye la marca de
    tiempo: no se ordena en memoria.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)
    return repositorio.listar_comentarios(id_proyecto, id_tarea)


def contar_por_tarea(id_proyecto, id_usuario):
    """Numero de comentarios vigentes de cada tarea del proyecto.

    Los suprimidos no se cuentan: el indicador de la tarjeta le dice al
    usuario cuanto hay por leer, y un comentario eliminado no es lectura
    pendiente. Recupera todos los comentarios del proyecto con una sola
    consulta y los agrupa en memoria.
    """
    _exigir_pertenencia(id_proyecto, id_usuario)

    conteo = {}
    for comentario in repositorio.listar_comentarios_del_proyecto(id_proyecto):
        if esta_eliminado(comentario):
            continue
        id_tarea = comentario.get("id_tarea")
        conteo[id_tarea] = conteo.get(id_tarea, 0) + 1
    return conteo
