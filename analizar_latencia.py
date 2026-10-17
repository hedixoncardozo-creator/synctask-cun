"""Analiza la campana de latencia del REQ-12 a partir del registro del sistema.

Se ejecuta en el servidor, despues de la campana:

    cd ~/synctask && journalctl -u synctask --since today --no-pager \
        | python3 analizar_latencia.py

Lee las lineas que la aplicacion dejo con el prefijo MEDICION_REQ12, calcula
los estadisticos y escribe un CSV con las mediciones individuales, que es lo
que se adjunta al informe como evidencia cruda.

Sobre la mediana y los percentiles: se informan ademas del promedio porque un
promedio solo esconde el caso malo. Si diecinueve mediciones dan 90 ms y una da
cuatro segundos, el promedio sigue pareciendo excelente y el requisito esta
incumplido. El criterio del REQ-05 es un techo, no una media, de modo que lo
que decide es el maximo y cuantas mediciones lo superan.
"""

import csv
import json
import statistics
import sys
from datetime import datetime

PREFIJO = "MEDICION_REQ12 "
CRITERIO_MS = 3000.0  # REQ-05: el cambio debe verse en menos de tres segundos


def leer(flujo):
    mediciones = []
    for linea in flujo:
        posicion = linea.find(PREFIJO)
        if posicion == -1:
            continue
        crudo = linea[posicion + len(PREFIJO):].strip()
        try:
            mediciones.append(json.loads(crudo))
        except json.JSONDecodeError:
            # Una linea truncada por el registro no debe tumbar el analisis;
            # se descarta y se informa al final cuantas se perdieron.
            mediciones.append(None)
    return mediciones


def main():
    crudas = leer(sys.stdin)
    descartadas = sum(1 for m in crudas if m is None)
    mediciones = [m for m in crudas if m]

    if not mediciones:
        print("No se encontro ninguna medicion en la entrada.")
        print("Verifique que la campana se ejecuto y que el periodo del")
        print("journalctl incluye el momento en que se hizo.")
        return 1

    valores = [m["ms"] for m in mediciones]
    valores_ordenados = sorted(valores)
    n = len(valores)

    def percentil(p):
        if n == 1:
            return valores_ordenados[0]
        posicion = (n - 1) * p / 100.0
        inferior = int(posicion)
        superior = min(inferior + 1, n - 1)
        peso = posicion - inferior
        return valores_ordenados[inferior] * (1 - peso) + valores_ordenados[superior] * peso

    incumplen = [v for v in valores if v >= CRITERIO_MS]

    print()
    print("=" * 62)
    print("REQ-12 - Campana de latencia de sincronizacion")
    print("=" * 62)
    print(f"Mediciones validas      : {n}")
    if descartadas:
        print(f"Lineas descartadas      : {descartadas} (ilegibles en el registro)")
    print()
    print(f"Minimo                  : {min(valores):8.1f} ms")
    print(f"Mediana                 : {statistics.median(valores):8.1f} ms")
    print(f"Promedio                : {statistics.fmean(valores):8.1f} ms")
    if n > 1:
        print(f"Desviacion tipica       : {statistics.stdev(valores):8.1f} ms")
    print(f"Percentil 95            : {percentil(95):8.1f} ms")
    print(f"Maximo                  : {max(valores):8.1f} ms")
    print()
    print(f"Criterio REQ-05         : {CRITERIO_MS:8.1f} ms")
    print(f"Mediciones que lo superan: {len(incumplen)} de {n}")
    if max(valores) < CRITERIO_MS:
        margen = CRITERIO_MS / max(valores)
        print()
        print(f"RESULTADO: se cumple. El peor caso es {margen:.0f} veces mas")
        print(f"rapido que el techo exigido.")
    else:
        print()
        print("RESULTADO: NO se cumple. Revise los casos que superan el techo")
        print("antes de informar el requisito como verificado.")

    print()
    print("Reparto por tipo de accion:")
    por_accion = {}
    for m in mediciones:
        por_accion.setdefault(m.get("accion", "desconocida"), []).append(m["ms"])
    for accion, lista in sorted(por_accion.items()):
        print(
            f"  {accion:<20} n={len(lista):<3} "
            f"mediana={statistics.median(lista):7.1f} ms  "
            f"max={max(lista):7.1f} ms"
        )

    nombre = f"latencia_req12_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    with open(nombre, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(["n", "momento", "accion", "milisegundos", "cumple"])
        for i, m in enumerate(mediciones, start=1):
            escritor.writerow(
                [i, m.get("momento", ""), m.get("accion", ""), m["ms"],
                 "si" if m["ms"] < CRITERIO_MS else "NO"]
            )
    print()
    print(f"Mediciones individuales escritas en: {nombre}")
    print("Ese archivo es la evidencia cruda que se adjunta al informe.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
