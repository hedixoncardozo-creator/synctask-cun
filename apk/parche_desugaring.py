"""Activa el core library desugaring en el Gradle que genera Flet (REQ-13).

Por que hace falta
------------------
`flet-notifications` envuelve a `flutter_local_notifications`, y esa biblioteca
usa clases de fecha y hora de Java 8 que no existen en las versiones viejas de
Android. Para cubrirlas hay que activar el *core library desugaring*, que
traduce esas clases a algo que el Android viejo entiende. Sin eso, la
compilacion se detiene asi:

    Execution failed for task ':app:checkReleaseAarMetadata'.
    > Dependency ':flutter_local_notifications' requires core library
      desugaring to be enabled for :app.

Por que un script y no una linea en pyproject.toml
--------------------------------------------------
Porque Flet 0.86.5 no tiene ninguna opcion para esto: no hay clave en
pyproject.toml ni mencion del desugaring en su codigo. El archivo que hay que
tocar, `build/flutter/android/app/build.gradle.kts`, lo *genera* Flet a partir
de una plantilla, de modo que cualquier cambio a mano desaparece en la
siguiente compilacion limpia.

De ahi este script: hace el cambio siempre igual y se puede repetir. Es
idempotente —si el archivo ya esta parchado no hace nada— asi que no pasa nada
por ejecutarlo de mas.

Como se usa

    cd apk
    flet build apk --yes -vv          # falla en checkReleaseAarMetadata
    python parche_desugaring.py       # aplica el parche
    flet build apk --yes -vv          # ahora si

La primera compilacion no se puede saltar: el archivo a parchar no existe
hasta que Flet genera el proyecto Flutter.

Si algun dia Flet trae soporte propio para el desugaring, este script sobra.
"""

import re
import sys
from pathlib import Path

RUTA = Path("build/flutter/android/app/build.gradle.kts")

# La version la documenta flutter_local_notifications para su rama 22.x. No se
# pone "la ultima" a proposito: toda esta sesion de compilacion se fue en
# perseguir paquetes que se actualizaron solos y rompieron la compatibilidad.
BIBLIOTECA = "com.android.tools:desugar_jdk_libs:2.1.4"

MARCA_OPCION = "isCoreLibraryDesugaringEnabled"
MARCA_DEPENDENCIA = "coreLibraryDesugaring("


def main() -> int:
    if not RUTA.exists():
        print(f"No encuentro {RUTA}")
        print()
        print("Ese archivo lo genera Flet. Ejecute primero la compilacion:")
        print("    flet build apk --yes -vv")
        print("y vuelva a lanzar este script cuando falle.")
        return 1

    texto = RUTA.read_text(encoding="utf-8")
    original = texto

    # 1) Activar la opcion dentro del bloque compileOptions.
    if MARCA_OPCION in texto:
        print("[  ya  ] la opcion de desugaring ya estaba activada")
    else:
        # Se ancla en targetCompatibility, que la plantilla de Flet siempre
        # escribe dentro de compileOptions, y se respeta su sangria.
        patron = re.compile(
            r"^(?P<sangria>[ \t]*)targetCompatibility = JavaVersion\.[A-Z_0-9]+$",
            re.MULTILINE,
        )
        coincidencia = patron.search(texto)
        if not coincidencia:
            print("[ FALLA] no encuentro 'targetCompatibility' en compileOptions")
            print("         la plantilla de Flet debe haber cambiado; "
                  "hay que revisar este script")
            return 1
        sangria = coincidencia.group("sangria")
        texto = patron.sub(
            lambda m: f"{m.group(0)}\n{sangria}{MARCA_OPCION} = true",
            texto,
            count=1,
        )
        print("[  OK  ] activada la opcion en compileOptions")

    # 2) Agregar la biblioteca al bloque dependencies.
    if MARCA_DEPENDENCIA in texto:
        print("[  ya  ] la biblioteca de desugaring ya estaba declarada")
    else:
        linea = f'    coreLibraryDesugaring("{BIBLIOTECA}")'
        if re.search(r"^dependencies\s*\{\s*\}\s*$", texto, re.MULTILINE):
            # El caso normal: Flet deja el bloque vacio al final del archivo.
            texto = re.sub(
                r"^dependencies\s*\{\s*\}\s*$",
                "dependencies {\n" + linea + "\n}",
                texto,
                count=1,
                flags=re.MULTILINE,
            )
        elif re.search(r"^dependencies\s*\{", texto, re.MULTILINE):
            texto = re.sub(
                r"^(dependencies\s*\{)",
                r"\1\n" + linea,
                texto,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            texto = texto.rstrip() + "\n\ndependencies {\n" + linea + "\n}\n"
        print(f"[  OK  ] declarada la biblioteca {BIBLIOTECA}")

    if texto == original:
        print()
        print("Nada que hacer: el archivo ya estaba parchado.")
        return 0

    # El reemplazo del bloque `dependencies {}` se come el salto de linea
    # final. A Gradle le da igual, pero un archivo sin el ultimo salto ensucia
    # cualquier diff posterior.
    if not texto.endswith("\n"):
        texto += "\n"

    RUTA.write_text(texto, encoding="utf-8")

    # Comprobacion posterior: no basta con haber escrito, hay que verificar que
    # lo escrito es lo que se queria.
    final = RUTA.read_text(encoding="utf-8")
    if MARCA_OPCION not in final or MARCA_DEPENDENCIA not in final:
        print()
        print("[ FALLA] el archivo se escribio pero no quedo completo")
        return 1

    print()
    print(f"Listo. Parchado {RUTA}")
    print("Ahora vuelva a lanzar:  flet build apk --yes -vv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
