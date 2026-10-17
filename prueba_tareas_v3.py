"""Verifica edicion y supresion de tareas, vencimientos y sesion recordada.

Se ejecuta como las demas, desde la raiz del proyecto:

    cd ~/synctask && source ~/venv/bin/activate && python3 prueba_tareas_v3.py

Cubre lo que se anadio en esta version: que una tarea se pueda corregir sin
cambiarle el estado por accidente, que al eliminarla desaparezca del tablero
pero sobreviva su conversacion, que el calculo de vencimiento trate bien los
casos de borde, y que el testigo de sesion solo sirva para su dueno.
"""

import traceback
from datetime import date, datetime, timedelta

import main as interfaz
from datos import repositorio
from modulos import comentarios, proyectos, recordar, sesion, tareas

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


class PreferenciasFalsas:
    """Doble del almacenamiento del dispositivo."""

    def __init__(self):
        self.datos = {}

    def set(self, clave, valor):
        self.datos[clave] = valor
        return True

    def get(self, clave):
        return self.datos.get(clave)

    def remove(self, clave):
        self.datos.pop(clave, None)
        return True


def main():
    page = PaginaFalsa()
    print(f"\nVerificacion de la version 3 - ejecucion {MARCA}")
    print("=" * 68)

    clave = "ClaveDePrueba123"
    ana = sesion.registrar_usuario("Ana Tercera", f"ana.v3.{MARCA}@cun.edu.co",
                                   clave, True)
    luis = sesion.registrar_usuario("Luis Tercero", f"luis.v3.{MARCA}@cun.edu.co",
                                    clave, True)

    proyecto = proyectos.crear("Proyecto v3", "prueba_tareas_v3.py", ana)
    id_proyecto = proyecto["PK"].split("#", 1)[1]
    proyectos.unirse(proyecto["codigo_acceso"], luis)

    ayer = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    manana = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    hoy = date.today().strftime("%Y-%m-%d")

    print("\nCalculo de vencimiento")

    comprobar("Una tarea con fecha de ayer esta vencida",
              interfaz.esta_vencida({"fecha_limite": ayer,
                                     "estado": "Pendiente"}))
    comprobar("Una tarea de ayer pero TERMINADA no se marca vencida",
              not interfaz.esta_vencida({"fecha_limite": ayer,
                                         "estado": "Terminado"}),
              "ya no es un problema que resolver; marcarla solo anadiria ruido")
    comprobar("Una tarea que vence hoy todavia no esta vencida",
              not interfaz.esta_vencida({"fecha_limite": hoy,
                                         "estado": "Pendiente"}))
    comprobar("Una tarea sin fecha nunca esta vencida",
              not interfaz.esta_vencida({"fecha_limite": "", "estado": "Pendiente"}))
    comprobar("Una fecha corrupta no rompe el calculo",
              not interfaz.esta_vencida({"fecha_limite": "manana",
                                         "estado": "Pendiente"}),
              "un dato malo en la tabla no debe tumbar el tablero")

    etiqueta, _color = interfaz.texto_de_plazo({"fecha_limite": hoy,
                                                "estado": "Pendiente"})
    comprobar("El plazo se describe en lenguaje natural", etiqueta == "vence hoy",
              f"para hoy dice: «{etiqueta}»")

    print("\nEdicion de tareas (REQ-03)")

    tarea = tareas.crear(page, id_proyecto, ana["id_usuario"],
                         titulo="Titulo con herror", descripcion="borrador",
                         responsable=ana["nombre"], fecha_limite=manana)
    id_tarea = tarea["SK"].split("#", 1)[1]
    tareas.cambiar_estado(page, id_proyecto, ana["id_usuario"], id_tarea,
                          "En progreso")

    editada = tareas.editar(page, id_proyecto, luis["id_usuario"], id_tarea,
                            titulo="Titulo corregido",
                            descripcion="version final",
                            responsable=luis["nombre"], fecha_limite=ayer)
    comprobar("Un integrante puede corregir la tarea de otro",
              editada["titulo"] == "Titulo corregido",
              "una tarea es un acuerdo del equipo, no propiedad de quien la creo")
    comprobar("La edicion NO altera el estado de la tarea",
              editada.get("estado") == "En progreso",
              "corregir un titulo no debe mover la tarea de columna")
    comprobar("La edicion deja marca de fecha_edicion",
              bool(editada.get("fecha_edicion")))
    comprobar("La tarea editada queda vencida con la fecha nueva",
              interfaz.esta_vencida(editada))

    try:
        tareas.editar(page, id_proyecto, ana["id_usuario"], id_tarea, titulo="  ",
                      descripcion="", responsable="", fecha_limite="")
        comprobar("Una tarea sin titulo se rechaza", False, "se permitio")
    except ValueError as error:
        comprobar("Una tarea sin titulo se rechaza", True, f"rechazado: {error}")

    print("\nSupresion de tareas y efecto en el tablero")

    comentarios.agregar(page, id_proyecto, ana["id_usuario"], ana["nombre"],
                        id_tarea, "Comentario que debe sobrevivir.")

    tareas.eliminar(page, id_proyecto, ana["id_usuario"], id_tarea)
    todas = tareas.listar(id_proyecto, ana["id_usuario"])
    vigentes = tareas.vigentes(todas)

    comprobar("La tarea eliminada desaparece del tablero",
              all(t["SK"].split("#", 1)[1] != id_tarea for t in vigentes),
              f"en la tabla hay {len(todas)}, vigentes {len(vigentes)}")
    comprobar("El elemento sigue en la tabla, marcado",
              any(t["SK"].split("#", 1)[1] == id_tarea and t.get("eliminada")
                  for t in todas))

    hilo = comentarios.listar(id_proyecto, ana["id_usuario"], id_tarea)
    comprobar("La conversacion de la tarea sobrevive a su supresion",
              len(hilo) == 1,
              "borrarla de verdad dejaria comentarios huerfanos imposibles de alcanzar")

    try:
        tareas.eliminar(page, id_proyecto, ana["id_usuario"], id_tarea)
        comprobar("No se puede eliminar dos veces", False, "se permitio")
    except ValueError as error:
        comprobar("No se puede eliminar dos veces", True, f"rechazado: {error}")

    print("\nSesion recordada (REQ-11)")

    preferencias = PreferenciasFalsas()
    testigo = recordar.recordar(preferencias, ana["id_usuario"])
    comprobar("Al ingresar se emite un testigo y se guarda en el dispositivo",
              bool(testigo) and preferencias.get(recordar.CLAVE) == testigo,
              f"longitud del testigo: {len(testigo)} caracteres")
    comprobar("El testigo no contiene el identificador del usuario",
              ana["id_usuario"] not in testigo,
              "si lo contuviera, se podria deducir o falsificar")

    recuperado = recordar.recuperar(preferencias)
    comprobar("Al reabrir, el testigo devuelve al mismo usuario",
              recuperado and recuperado["id_usuario"] == ana["id_usuario"],
              f"recuperado: {(recuperado or {}).get('nombre')}")
    comprobar("El perfil recuperado no trae la clave derivada ni la sal",
              recuperado is not None
              and "clave_derivada" not in recuperado and "sal" not in recuperado)

    falsas = PreferenciasFalsas()
    falsas.set(recordar.CLAVE, "testigo-inventado-que-no-existe")
    comprobar("Un testigo inventado no abre ninguna sesion",
              recordar.recuperar(falsas) is None)
    comprobar("Un testigo invalido se borra del dispositivo",
              falsas.get(recordar.CLAVE) is None,
              "asi no se reintenta la misma consulta inutil en cada apertura")

    recordar.olvidar(preferencias)
    comprobar("Cerrar sesion borra el testigo del dispositivo",
              preferencias.get(recordar.CLAVE) is None)
    comprobar("Cerrar sesion tambien revoca el testigo en la base de datos",
              repositorio.usuario_de_testigo(testigo) is None,
              "si no se revocara, el dispositivo conservaria una llave valida")

    print("\n" + "=" * 68)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    print(f"Proyecto de prueba: {id_proyecto}")
    if exitosas == total:
        print("\nLa version 3 responde segun lo esperado.")
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
