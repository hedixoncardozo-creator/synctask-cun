"""Verificacion del indice de proyectos por usuario contra DynamoDB (REQ-02).

Se ejecuta igual que las dos anteriores, desde la raiz del proyecto:

    cd ~/synctask && source ~/venv/bin/activate && python3 prueba_proyectos.py

La comprobacion que importa es la numero 5: un usuario solo debe ver en su
lista los proyectos a los que pertenece. Si el espejo se escribiera bajo una
clave equivocada, un usuario veria proyectos ajenos en su pantalla de inicio,
que es una fuga de informacion y no un simple error de presentacion.

La comprobacion 1 imprime ademas las firmas reales de los modulos que usa la
aplicacion. Es la forma mas rapida de detectar que main.py llama a una funcion
con un nombre de parametro que ya no existe.
"""

import inspect
import traceback
from datetime import datetime

from datos import repositorio
from modulos import comentarios, proyectos, sesion, tareas

MARCA = datetime.now().strftime("%Y%m%d%H%M%S")

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    marca = "  OK  " if condicion else " FALLA"
    print(f"[{marca}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


def main():
    print(f"\nVerificacion del indice de proyectos - ejecucion {MARCA}")
    print("=" * 68)

    print("\nFirmas que utiliza main.py")
    for modulo, nombres in (
        (tareas, ("crear", "cambiar_estado", "listar", "suscribir", "cancelar_suscripcion")),
        (comentarios, ("agregar", "listar", "contar_por_tarea")),
        (sesion, ("registrar_usuario", "autenticar")),
        (repositorio, ("listar_integrantes", "obtener_proyecto", "listar_proyectos_de_usuario")),
    ):
        for nombre in nombres:
            funcion = getattr(modulo, nombre, None)
            if funcion is None:
                comprobar(f"{modulo.__name__}.{nombre} existe", False, "no esta definida")
            else:
                print(f"         {modulo.__name__}.{nombre}{inspect.signature(funcion)}")
    comprobar(
        "Todas las funciones que invoca la interfaz estan definidas",
        all(
            getattr(m, n, None) is not None
            for m, ns in (
                (tareas, ("crear", "cambiar_estado", "listar", "suscribir", "cancelar_suscripcion")),
                (comentarios, ("agregar", "listar", "contar_por_tarea")),
                (sesion, ("registrar_usuario", "autenticar")),
                (repositorio, ("listar_integrantes", "obtener_proyecto", "listar_proyectos_de_usuario")),
            )
            for n in ns
        ),
    )

    clave = "ClaveDePrueba123"
    ana = sesion.registrar_usuario("Ana Indice", f"ana.i.{MARCA}@cun.edu.co", clave, True)
    luis = sesion.registrar_usuario("Luis Indice", f"luis.i.{MARCA}@cun.edu.co", clave, True)

    print("\nAlta del proyecto y del espejo de lectura")

    proyecto = proyectos.crear("Proyecto con indice", "Creado por prueba_proyectos.py", ana)
    id_proyecto = proyecto["PK"].split("#", 1)[1]
    codigo = proyecto.get("codigo_acceso")

    mios_de_ana = proyectos.listar_mios(ana)
    comprobar(
        "El autor ve su proyecto recien creado",
        any(e.get("id_proyecto") == id_proyecto for e in mios_de_ana),
        f"proyectos de Ana: {len(mios_de_ana)} · codigo: {codigo}",
    )
    comprobar(
        "El autor queda con el rol de administrador",
        any(
            e.get("id_proyecto") == id_proyecto and e.get("rol") == "administrador"
            for e in mios_de_ana
        ),
    )

    print("\nVinculacion por codigo (REQ-02)")

    comprobar(
        "Luis no ve el proyecto antes de unirse",
        not any(e.get("id_proyecto") == id_proyecto for e in proyectos.listar_mios(luis)),
    )

    proyectos.unirse(codigo, luis)
    comprobar(
        "Luis ve el proyecto despues de unirse, como integrante",
        any(
            e.get("id_proyecto") == id_proyecto and e.get("rol") == "integrante"
            for e in proyectos.listar_mios(luis)
        ),
    )

    antes = len(proyectos.listar_mios(luis))
    proyectos.unirse(codigo, luis)
    comprobar(
        "Volver a usar el codigo no duplica el proyecto ni falla",
        len(proyectos.listar_mios(luis)) == antes,
        f"proyectos de Luis: {antes}",
    )

    print("\nAislamiento entre usuarios")

    ajena = sesion.registrar_usuario("Carla Ajena", f"carla.i.{MARCA}@cun.edu.co", clave, True)
    comprobar(
        "Una usuaria que no se unio a nada tiene la lista vacia",
        proyectos.listar_mios(ajena) == [],
        f"proyectos de Carla: {len(proyectos.listar_mios(ajena))}",
    )

    try:
        proyectos.obtener(id_proyecto, ajena)
        comprobar("Una usuaria ajena no puede abrir el tablero", False, "se permitio")
    except tareas.ErrorDePermiso as error:
        comprobar("Una usuaria ajena no puede abrir el tablero", True, f"rechazado: {error}")

    print("\nCodigos invalidos")

    try:
        proyectos.unirse("NOEXISTE", ajena)
        comprobar("Un codigo inexistente se rechaza con mensaje claro", False, "se permitio")
    except proyectos.ErrorDeCodigo as error:
        comprobar("Un codigo inexistente se rechaza con mensaje claro", True, f"{error}")

    print("\nMetadatos para el encabezado del tablero")

    metadatos = proyectos.obtener(id_proyecto, ana)
    comprobar(
        "obtener() devuelve nombre y codigo de acceso",
        bool(metadatos) and metadatos.get("nombre") and metadatos.get("codigo_acceso"),
        f"nombre: {(metadatos or {}).get('nombre')} · codigo: {(metadatos or {}).get('codigo_acceso')}",
    )

    print("\n" + "=" * 68)
    total = len(_resultados)
    exitosas = sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    print(f"Proyecto de prueba: {id_proyecto}")
    if exitosas == total:
        print("\nEl indice de proyectos por usuario responde segun lo esperado.")
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