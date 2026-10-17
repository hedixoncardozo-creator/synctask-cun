"""Verifica la descripcion de cambios del tablero (REQ-05).

No toca DynamoDB ni Flet: compara listas de diccionarios y comprueba las
frases. Se ejecuta en cualquier parte, incluso sin credenciales:

    cd ~/synctask && python3 prueba_avisos.py
"""

from modulos import avisos

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


def tarea(id_tarea, titulo, estado="Pendiente", **extra):
    base = {"SK": f"TAREA#{id_tarea}", "titulo": titulo, "estado": estado,
            "descripcion": "", "responsable": "Ana", "fecha_limite": ""}
    base.update(extra)
    return base


def main():
    print("\nVerificacion de la descripcion de cambios")
    print("=" * 64)

    a = tarea("1", "Redactar el marco teorico")
    b = tarea("2", "Preparar la sustentacion")

    print("\nTipos de cambio")

    f = avisos.describir([a], [a, b])
    comprobar("Una tarea nueva se anuncia por su titulo",
              f == ["Nueva tarea: «Preparar la sustentacion»"], f"{f}")

    f = avisos.describir([a, b], [a])
    comprobar("Una tarea eliminada se anuncia",
              f == ["Se elimino «Preparar la sustentacion»"], f"{f}")

    movida = tarea("1", "Redactar el marco teorico", estado="Terminado")
    f = avisos.describir([a], [movida])
    comprobar("Un cambio de estado dice a donde fue",
              f == ["«Redactar el marco teorico» paso a Terminado"], f"{f}")

    editada = tarea("1", "Redactar el marco teorico v2")
    f = avisos.describir([a], [editada])
    comprobar("Una edicion se anuncia como edicion", "Se edito" in f[0], f"{f}")

    f = avisos.describir([a], [a], {"1": 0}, {"1": 1})
    comprobar("Un comentario nuevo se anuncia aunque la tarea no cambie",
              f == ["Nuevo comentario en «Redactar el marco teorico»"], f"{f}")

    f = avisos.describir([a], [a], {"1": 1}, {"1": 4})
    comprobar("Varios comentarios de golpe se cuentan",
              f == ["3 comentarios nuevos en «Redactar el marco teorico»"],
              f"{f}")

    print("\nCasos que no deben generar ruido")

    comprobar("Sin cambios no hay frases", avisos.describir([a], [a]) == [])
    comprobar("Un tablero vacio que sigue vacio no dice nada",
              avisos.describir([], []) == [])
    comprobar("Un cambio de estado NO se anuncia ademas como edicion",
              len(avisos.describir([a], [movida])) == 1,
              "anunciar las dos cosas duplicaria el aviso del mismo hecho")
    comprobar("Un comentario borrado no genera aviso",
              avisos.describir([a], [a], {"1": 3}, {"1": 2}) == [],
              "el conteo baja; avisar de eso solo confundiria")

    print("\nRedaccion del resumen")

    comprobar("Una sola frase se muestra tal cual",
              avisos.resumir(["Nueva tarea: «X»"]) == "Nueva tarea: «X»")

    muchas = [f"cambio {i}" for i in range(7)]
    r = avisos.resumir(muchas)
    comprobar("Con muchos cambios se muestran tres y se cuenta el resto",
              r.endswith("y 4 cambios mas") and r.count("·") == 3,
              f"{r}")
    comprobar("Un solo cambio sobrante se dice en singular",
              avisos.resumir([f"c{i}" for i in range(4)]).endswith(
                  "y 1 cambio mas"))

    largo = tarea("3", "Un titulo larguisimo que no cabe en una notificacion "
                       "de telefono porque sigue y sigue")
    f = avisos.describir([], [largo])
    comprobar("Un titulo muy largo se recorta", "…" in f[0] and len(f[0]) < 60,
              f"{f[0]}")

    print("\nTitulo y cuerpo de la notificacion del sistema")

    t, c = avisos.titulo_y_cuerpo(["Nueva tarea: «X»"], "Entrega 2")
    comprobar("El proyecto va en el titulo y el cambio en el cuerpo",
              t == "Entrega 2" and c == "Nueva tarea: «X»",
              f"titulo={t!r} cuerpo={c!r}")
    t, c = avisos.titulo_y_cuerpo([], "Entrega 2")
    comprobar("Sin cambios no hay notificacion que mostrar", t == "" and c == "")

    print("\n" + "=" * 64)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    return 0 if exitosas == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
