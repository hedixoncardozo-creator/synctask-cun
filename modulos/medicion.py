"""Instrumentacion de latencia de sincronizacion (REQ-05, REQ-12).

Que se mide exactamente, porque de esto depende que el numero signifique algo:

    t0  el instante en que el servidor recibe la accion de quien la origina,
        antes de escribir en DynamoDB.
    t1  el instante en que otra sesion del mismo proyecto ha terminado de
        reconstruir su pantalla con el cambio ya incorporado.

El intervalo t1 - t0 cubre entonces: la escritura en DynamoDB, la difusion por
PubSub, la relectura que hace la sesion receptora y la reconstruccion de sus
controles. Es decir, todo el camino que el sistema controla.

Queda fuera un tramo: el viaje del paquete por WebSocket hasta el dispositivo y
el repintado de Flutter en pantalla. No se mide aqui porque medirlo exigiria
instrumentar el navegador, y fingir que se midio seria peor que declararlo. Ese
tramo se acota aparte con el tiempo de ida y vuelta de la red, y se suma en el
informe. Con el criterio del REQ-05 en tres segundos, la distincion no cambia
la conclusion, pero si cambia la honestidad del dato.

Por que un reloj monotonico y no la hora del sistema: las dos sesiones viven en
el mismo proceso de Python, de modo que comparten reloj y no hay desfase entre
maquinas que corregir. time.monotonic_ns() ademas no retrocede si el servidor
sincroniza su hora por NTP a mitad de la campana, cosa que datetime.now() si
haria y que corromperia una medicion sin dejar rastro.

Supuesto del protocolo: un solo actor a la vez. La campana se ejecuta con una
persona actuando y la otra observando, de modo que cada recepcion corresponde
sin ambiguedad a la ultima emision registrada para ese proyecto. Si dos
personas actuaran simultaneamente, las mediciones se mezclarian; por eso el
protocolo lo prohibe y por eso se registra el autor de cada emision.
"""

import json
import threading
import time
import uuid

# Ultima emision registrada por proyecto. Cabe una sola porque el protocolo de
# medicion exige un actor a la vez; no es una cola de eventos.
_emisiones = {}
_candado = threading.Lock()

ACTIVA = True


def marcar_emision(id_proyecto, id_autor, accion):
    """Registra el instante en que empieza una accion que otros deben ver.

    Se invoca ANTES de escribir en la base de datos, no despues: lo que importa
    es cuanto tarda el companero en enterarse desde que la persona actuo, no
    desde que la escritura ya estaba resuelta.
    """
    if not ACTIVA:
        return
    with _candado:
        _emisiones[id_proyecto] = {
            "t0": time.monotonic_ns(),
            "id_evento": uuid.uuid4().hex[:8],
            "autor": id_autor,
            "accion": accion,
        }


def marcar_recepcion(id_proyecto, id_observador):
    """Registra que una sesion ajena ya refleja el cambio. Devuelve los ms.

    Devuelve None —y no registra nada— cuando quien recibe es el mismo que
    origino el cambio. Esa sesion tambien recibe la difusion, pero medir su
    propia actualizacion no responde a la pregunta del REQ-05, que es cuanto
    tardan en verlo *los demas*.
    """
    if not ACTIVA:
        return None
    with _candado:
        emision = _emisiones.get(id_proyecto)
    if not emision or emision["autor"] == id_observador:
        return None

    milisegundos = (time.monotonic_ns() - emision["t0"]) / 1_000_000.0
    registro = {
        "id_evento": emision["id_evento"],
        "accion": emision["accion"],
        "ms": round(milisegundos, 2),
        "proyecto": id_proyecto[:8],
        "momento": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # El prefijo permite extraer las mediciones del registro del sistema sin
    # confundirlas con cualquier otra linea que imprima la aplicacion.
    print("MEDICION_REQ12 " + json.dumps(registro, ensure_ascii=True), flush=True)
    return milisegundos