"""Verifica la edicion y la supresion de comentarios contra DynamoDB (REQ-06).

Se ejecuta como las demas, desde la raiz del proyecto:

    cd ~/synctask && source ~/venv/bin/activate && python3 prueba_edicion.py

Las comprobaciones que de verdad importan son las negativas: que un tercero no
pueda tocar el comentario ajeno, y que no se pueda editar ni volver a borrar
uno ya suprimido. La autoria se exige con una ConditionExpression en DynamoDB,
de modo que estas pruebas no estan verificando un "if" de Python sino que la
base de datos rechaza la escritura. Esa diferencia es la que hace que el
control no se pueda saltar por otro camino.
"""

import traceback
from datetime import datetime

from modulos import comentarios, proyectos, sesion, tareas

MARCA = datetime.now().strftime("%Y%m%d%H%M%S")
_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


class PubSubFalso:
    def send_all_on_topic(self, tema, mensaje):
        pass

    def subscribe_topic(self, tema, manejador):
        pass

    def unsubscribe_topic(self, tema):
        pass


class PaginaFalsa:
    def __init__(self):
        self.pubsub = PubSubFalso()


def main():
    page = PaginaFalsa()
    print(f"\nVerificacion de edicion y supresion - ejecucion {MARCA}")
    print("=" * 66)

    clave_acceso = "ClaveDePrueba123"
    ana = sesion.registrar_usuario(
        "Ana Editora", f"ana.ed.{MARCA}@cun.edu.co", clave_acceso, True)
    luis = sesion.registrar_usuario(
        "Luis Lector", f"luis.ed.{MARCA}@cun.edu.co", clave_acceso, True)

    proyecto = proyectos.crear("Proyecto de edicion", "prueba_edicion.py", ana)
    id_proyecto = proyecto["PK"].split("#", 1)[1]
    proyectos.unirse(proyecto["codigo_acceso"], luis)

    tarea = tareas.crear(
        page, id_proyecto, ana["id_usuario"],
        titulo="Tarea con conversacion", descripcion="",
        responsable=ana["nombre"], fecha_limite="2026-10-30")
    id_tarea = tarea["SK"].split("#", 1)[1]

    comentario = comentarios.agregar(
        page, id_proyecto, ana["id_usuario"], ana["nombre"], id_tarea,
        "Primera version del comentario.")
    clave = comentario["SK"]

    print("\nEdicion del comentario propio")

    editado = comentarios.editar(
        page, id_proyecto, ana["id_usuario"], clave, "Version corregida.")
    comprobar("El autor puede editar su comentario",
              editado["texto"] == "Version corregida.")
    comprobar("La edicion deja marca de fecha_edicion",
              bool(editado.get("fecha_edicion")),
              "sin esa marca, un comentario editado se haria pasar por el original")
    comprobar("comentarios.fue_editado lo reconoce",
              comentarios.fue_editado(editado))

    print("\nControl de autoria (REQ-07)")

    try:
        comentarios.editar(page, id_proyecto, luis["id_usuario"], clave, "Intruso")
        comprobar("Un tercero NO puede editar el comentario ajeno", False,
                  "la operacion se permitio y no debia")
    except comentarios.ErrorDeAutoria as error:
        comprobar("Un tercero NO puede editar el comentario ajeno", True,
                  f"rechazado por la base de datos: {error}")

    try:
        comentarios.eliminar(page, id_proyecto, luis["id_usuario"], clave)
        comprobar("Un tercero NO puede eliminar el comentario ajeno", False,
                  "la operacion se permitio y no debia")
    except comentarios.ErrorDeAutoria as error:
        comprobar("Un tercero NO puede eliminar el comentario ajeno", True,
                  f"rechazado por la base de datos: {error}")

    try:
        comentarios.editar(page, id_proyecto, ana["id_usuario"], clave, "   ")
        comprobar("Una edicion vacia se rechaza", False, "se permitio")
    except ValueError as error:
        comprobar("Una edicion vacia se rechaza", True, f"rechazado: {error}")

    print("\nSupresion suave")

    suprimido = comentarios.eliminar(page, id_proyecto, ana["id_usuario"], clave)
    comprobar("El autor puede eliminar su comentario",
              comentarios.esta_eliminado(suprimido))
    comprobar("La supresion vacia el texto", suprimido.get("texto") == "")
    comprobar("La supresion conserva autor y fecha original",
              suprimido.get("nombre_autor") == ana["nombre"]
              and bool(suprimido.get("fecha")),
              "esa huella es lo que sostiene la trazabilidad del aporte individual")

    try:
        comentarios.eliminar(page, id_proyecto, ana["id_usuario"], clave)
        comprobar("No se puede eliminar dos veces", False, "se permitio")
    except comentarios.ErrorDeAutoria as error:
        comprobar("No se puede eliminar dos veces", True, f"rechazado: {error}")

    try:
        comentarios.editar(page, id_proyecto, ana["id_usuario"], clave, "Resucitar")
        comprobar("No se puede editar un comentario suprimido", False, "se permitio")
    except comentarios.ErrorDeAutoria as error:
        comprobar("No se puede editar un comentario suprimido", True,
                  f"rechazado: {error}")

    print("\nEfecto en el hilo y en el indicador de la tarjeta")

    historial = comentarios.listar(id_proyecto, luis["id_usuario"], id_tarea)
    comprobar("El comentario suprimido sigue en el hilo, marcado",
              len(historial) == 1 and comentarios.esta_eliminado(historial[0]),
              "ocultarlo dejaria un hueco inexplicable en una conversacion ya leida")

    comentarios.agregar(page, id_proyecto, luis["id_usuario"], luis["nombre"],
                        id_tarea, "Comentario vigente de Luis.")
    conteo = comentarios.contar_por_tarea(id_proyecto, luis["id_usuario"])
    comprobar("El indicador de la tarjeta ignora los suprimidos",
              conteo.get(id_tarea) == 1,
              f"conteo={conteo} (hay 2 elementos en la tabla, 1 vigente)")

    print("\n" + "=" * 66)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    print(f"Proyecto de prueba: {id_proyecto}")
    if exitosas == total:
        print("\nLa edicion y la supresion responden segun lo esperado.")
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
