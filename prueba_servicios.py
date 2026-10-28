"""Verifica que los servicios del dispositivo se ejecuten de verdad.

En Flet 0.86 todos los metodos de todos los servicios son asincronos y
ninguno tiene variante sincrona. Llamarlos sin ejecutar devuelve una corrutina
que nadie atiende: la operacion no ocurre y no se levanta ningun error.

Este proyecto cayo tres veces en eso:

1. La sesion recordada no guardaba nada. Semanas sin que se notara, porque
   volver a escribir la contrasena parecia normal.
2. El portapapeles anunciaba "Codigo copiado" sin copiar. Se descubrio al
   intentar pegar el codigo en otra aplicacion.
3. La banda de conexion no aparecia nunca, porque recorrer la corrutina
   lanzaba TypeError dentro de un try que lo ocultaba.

El patron comun no es "se nos olvido un await". Es que **el fallo era
invisible**: la funcion no reventaba, simplemente no hacia nada. Por eso esta
prueba no comprueba que las funciones devuelvan algo, sino que el servicio
haya recibido la llamada.

    cd ~/synctask && python3 prueba_servicios.py
"""

import asyncio
import gc
import sys
import warnings

import main as app

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


class EstadoFalso:
    def __init__(self, bucle):
        self.bucle = bucle
        self.vibracion = None
        self.portapapeles = None


class VibracionFalsa:
    """Imita HapticFeedback: sus metodos son corrutinas, como los de verdad."""

    def __init__(self):
        self.suaves = 0
        self.fuertes = 0

    async def light_impact(self):
        self.suaves += 1

    async def medium_impact(self):
        self.fuertes += 1


class PortapapelesFalso:
    def __init__(self, fallar=False):
        self.contenido = None
        self.fallar = fallar

    async def set(self, valor):
        if self.fallar:
            raise RuntimeError("el dispositivo rechazo la copia")
        self.contenido = valor


async def principal():
    bucle = asyncio.get_running_loop()

    print("\nVerificacion de los servicios del dispositivo")
    print("=" * 64)

    print("\nVibracion")

    estado = EstadoFalso(bucle)
    estado.vibracion = VibracionFalsa()
    app.vibrar(estado)
    app.vibrar(estado, intenso=True)
    await asyncio.sleep(0.05)

    comprobar("El golpecito suave llega al dispositivo",
              estado.vibracion.suaves == 1,
              f"recibidos: {estado.vibracion.suaves} "
              "(antes era 0: la corrutina se descartaba)")
    comprobar("El golpecito fuerte tambien",
              estado.vibracion.fuertes == 1)

    sin_servicio = EstadoFalso(bucle)
    app.vibrar(sin_servicio)
    comprobar("Sin servicio de vibracion no revienta", True,
              "en el navegador no existe y la aplicacion debe seguir igual")

    print("\nPortapapeles")

    estado = EstadoFalso(bucle)
    estado.portapapeles = PortapapelesFalso()
    respuestas = []
    app.copiar_al_portapapeles(estado, "ABC123", respuestas.append)
    await asyncio.sleep(0.05)

    comprobar("El texto llega de verdad al portapapeles",
              estado.portapapeles.contenido == "ABC123",
              f"contenido: {estado.portapapeles.contenido!r}")
    comprobar("Y se informa exito UNA sola vez",
              respuestas == [True], f"{respuestas}")

    print("\nCuando el portapapeles falla")

    estado = EstadoFalso(bucle)
    estado.portapapeles = PortapapelesFalso(fallar=True)
    respuestas = []
    app.copiar_al_portapapeles(estado, "ABC123", respuestas.append)
    await asyncio.sleep(0.05)

    comprobar("Se informa el fallo, no un exito falso",
              respuestas == [False], f"{respuestas}")
    comprobar("Ese era el defecto: antes se anunciaba exito sin comprobarlo",
              respuestas != [True],
              "un mensaje de exito que no comprueba el exito esconde el error")

    estado = EstadoFalso(bucle)
    estado.portapapeles = None
    respuestas = []
    app.copiar_al_portapapeles(estado, "ABC123", respuestas.append)
    comprobar("Sin servicio tambien responde, y responde que no",
              respuestas == [False])

    sin_bucle = EstadoFalso(None)
    sin_bucle.portapapeles = PortapapelesFalso()
    respuestas = []
    app.copiar_al_portapapeles(sin_bucle, "ABC123", respuestas.append)
    comprobar("Sin bucle de eventos responde que no, no se cuelga",
              respuestas == [False])


def main():
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        asyncio.run(principal())
        gc.collect()

    print("\nCorrutinas sin ejecutar")
    sueltas = [str(a.message) for a in avisos
               if "never awaited" in str(a.message)]
    comprobar("Ninguna corrutina queda sin ejecutar", not sueltas,
              "\n         ".join(sueltas) if sueltas
              else "es el sintoma exacto de los tres defectos de este modulo")

    print("\n" + "=" * 64)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    return 0 if exitosas == total else 1


if __name__ == "__main__":
    sys.exit(main())
