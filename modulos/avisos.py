"""Describe en lenguaje natural que cambio en el tablero.

Por que compara dos fotos del tablero en lugar de leer el mensaje de PubSub:
el mensaje viaja con un contenido que depende de quien lo emitio y que ha ido
cambiando a medida que crecia la aplicacion. Comparar el antes y el despues no
depende de ese formato en absoluto; funciona igual para una tarea creada, una
movida de columna, una editada, una eliminada o un comentario nuevo, y seguira
funcionando si manana se anade otro tipo de cambio. Es ademas la unica forma de
describir con precision *que* cambio cuando llegan dos avisos muy seguidos.

El modulo no sabe nada de Flet a proposito: recibe listas de diccionarios y
devuelve cadenas. Asi se puede probar entero sin levantar servidor ni navegador,
que es como se verifico.
"""

LIMITE_DE_FRASES = 3


def _id(tarea):
    return tarea.get("SK", "").split("#", 1)[-1]


def _titulo(tarea):
    titulo = (tarea.get("titulo") or "").strip() or "una tarea"
    if len(titulo) > 38:
        titulo = titulo[:37].rstrip() + "…"
    return titulo


def describir(antes, despues, conteo_antes=None, conteo_despues=None):
    """Compara dos estados del tablero y devuelve las frases de los cambios.

    `antes` y `despues` son listas de tareas vigentes. Los conteos son
    diccionarios de identificador de tarea a numero de comentarios.

    Devuelve una lista de cadenas, ya redactadas para mostrarselas a una
    persona. Lista vacia si no cambio nada relevante.
    """
    conteo_antes = conteo_antes or {}
    conteo_despues = conteo_despues or {}

    por_id_antes = {_id(t): t for t in (antes or [])}
    por_id_despues = {_id(t): t for t in (despues or [])}

    frases = []

    # Tareas nuevas.
    for id_tarea, tarea in por_id_despues.items():
        if id_tarea not in por_id_antes:
            frases.append(f"Nueva tarea: «{_titulo(tarea)}»")

    # Tareas retiradas del tablero.
    for id_tarea, tarea in por_id_antes.items():
        if id_tarea not in por_id_despues:
            frases.append(f"Se elimino «{_titulo(tarea)}»")

    # Cambios sobre tareas que siguen estando.
    for id_tarea, tarea in por_id_despues.items():
        anterior = por_id_antes.get(id_tarea)
        if not anterior:
            continue
        if anterior.get("estado") != tarea.get("estado"):
            frases.append(
                f"«{_titulo(tarea)}» paso a {tarea.get('estado', '')}")
            continue
        cambio_algo = any(
            (anterior.get(campo) or "") != (tarea.get(campo) or "")
            for campo in ("titulo", "descripcion", "responsable", "fecha_limite")
        )
        if cambio_algo:
            frases.append(f"Se edito «{_titulo(tarea)}»")

    # Comentarios nuevos. Se informan aparte porque una tarea puede recibir un
    # comentario sin que ninguno de sus campos cambie.
    for id_tarea, cuantos in conteo_despues.items():
        previos = conteo_antes.get(id_tarea, 0)
        if cuantos > previos:
            tarea = por_id_despues.get(id_tarea)
            nombre = _titulo(tarea) if tarea else "una tarea"
            nuevos = cuantos - previos
            if nuevos == 1:
                frases.append(f"Nuevo comentario en «{nombre}»")
            else:
                frases.append(f"{nuevos} comentarios nuevos en «{nombre}»")

    return frases


def resumir(frases, limite=LIMITE_DE_FRASES):
    """Convierte varias frases en un solo mensaje corto.

    Un aviso con siete lineas no se lee: se cierra. Se muestran las primeras y
    se cuenta el resto, que es lo que hace cualquier aplicacion de mensajeria
    cuando llegan muchas cosas a la vez.
    """
    if not frases:
        return ""
    if len(frases) <= limite:
        return " · ".join(frases)
    visibles = frases[:limite]
    restantes = len(frases) - limite
    cola = "1 cambio mas" if restantes == 1 else f"{restantes} cambios mas"
    return " · ".join(visibles) + f" · y {cola}"


def titulo_y_cuerpo(frases, nombre_proyecto):
    """Parte el resumen en titulo y cuerpo para una notificacion del sistema.

    Una notificacion de Android con el titulo vacio o con todo el texto en el
    titulo se ve mal y se corta. El nombre del proyecto va arriba porque es lo
    que permite saber, sin abrir nada, si el aviso interesa ahora mismo.
    """
    if not frases:
        return "", ""
    if len(frases) == 1:
        return nombre_proyecto, frases[0]
    return nombre_proyecto, resumir(frases)
