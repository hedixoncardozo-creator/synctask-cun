"""Verifica la sesion recordada (REQ-11).

No toca DynamoDB ni necesita credenciales: sustituye el repositorio y el
almacenamiento del dispositivo por dobles, pero corre sobre un bucle de
eventos de verdad, que es donde estaba el defecto que motivo esta prueba.

    cd ~/synctask && python3 prueba_recordar.py

Que se comprueba y por que
--------------------------
La version anterior de modulos/recordar.py llamaba a las operaciones de
SharedPreferences sin `await`. En Flet 0.86 las tres son asincronas, asi que
no se guardaba nada y al recuperar se consultaba la tabla con la clave
`TESTIGO#<coroutine object ...>`. Nada fallaba a la vista: todo estaba
envuelto en try/except y el unico rastro era un RuntimeWarning en el registro.

Por eso aqui no basta con comprobar que las funciones "no revientan". Se
comprueba que el testigo hace el viaje completo —se guarda, se recupera y
devuelve el perfil correcto— y que no queda ninguna corrutina sin ejecutar.
"""

import asyncio
import gc
import sys
import threading
import warnings

from modulos import recordar

_resultados = []


def comprobar(descripcion, condicion, detalle=""):
    _resultados.append(bool(condicion))
    print(f"[{'  OK  ' if condicion else ' FALLA'}] {descripcion}")
    if detalle:
        print(f"         {detalle}")


class PreferenciasFalsas:
    """Almacenamiento del dispositivo. Asincrono, como el de verdad."""

    def __init__(self):
        self.datos = {}
        self.fallar = False

    async def set(self, clave, valor):
        if self.fallar:
            raise RuntimeError("almacenamiento no disponible")
        self.datos[clave] = valor
        return True

    async def get(self, clave):
        if self.fallar:
            raise RuntimeError("almacenamiento no disponible")
        return self.datos.get(clave)

    async def remove(self, clave):
        self.datos.pop(clave, None)
        return True


class RepositorioFalso:
    """Imita la tabla. Cuenta consultas: es lo que se quiere medir."""

    def __init__(self):
        # testigo -> {"id": str, "perfil": dict | None}
        # perfil None imita los testigos emitidos antes de guardar la copia.
        self.testigos = {}
        self.usuarios = {"u1": {"id_usuario": "u1", "nombre": "Ana",
                                "correo": "ana@cun.edu.co"}}
        self.revocados = []
        self.consultas = []          # lecturas del testigo
        self.perfiles_pedidos = []   # lecturas del perfil (la segunda consulta)
        self.caer = False
        self.contador = 0

    def crear_testigo(self, id_usuario, perfil=None):
        self.contador += 1
        testigo = f"testigo-{self.contador}"
        self.testigos[testigo] = {"id": id_usuario, "perfil": perfil}
        return testigo

    def sesion_de_testigo(self, testigo):
        self.consultas.append(testigo)
        if self.caer:
            raise RuntimeError("sin red")
        dato = self.testigos.get(testigo)
        if not dato:
            return None, None
        perfil = dato["perfil"]
        if perfil is None:
            return dato["id"], None
        return dato["id"], {"id_usuario": dato["id"],
                            "nombre": perfil.get("nombre", ""),
                            "correo": perfil.get("correo", "")}

    def obtener_usuario(self, id_usuario):
        self.perfiles_pedidos.append(id_usuario)
        return self.usuarios.get(id_usuario)

    def revocar_testigo(self, testigo):
        self.revocados.append(testigo)
        self.testigos.pop(testigo, None)


async def recuperar_esperando(bucle, prefs):
    """Envuelve la devolucion de llamada en algo que se pueda esperar."""
    futuro = bucle.create_future()

    def al_terminar(perfil):
        if not futuro.done():
            futuro.set_result(perfil)

    recordar.recuperar(bucle, prefs, al_terminar)
    return await asyncio.wait_for(futuro, timeout=5)


async def principal():
    bucle = asyncio.get_running_loop()
    repo = RepositorioFalso()
    recordar.repositorio = repo

    print("\nVerificacion de la sesion recordada")
    print("=" * 64)
    print("\nEl viaje completo del testigo")

    prefs = PreferenciasFalsas()

    # Como en el ingreso de verdad: desde un hilo de trabajo, no desde el bucle.
    emitido = {}

    def ingresar():
        emitido["testigo"] = recordar.recordar(
            bucle, prefs, {"id_usuario": "u1", "nombre": "Ana",
                           "correo": "ana@cun.edu.co"})

    hilo = threading.Thread(target=ingresar)
    hilo.start()
    hilo.join()
    await asyncio.sleep(0.1)

    comprobar("El testigo queda guardado en el dispositivo",
              prefs.datos.get(recordar.CLAVE) == emitido["testigo"],
              f"guardado={prefs.datos.get(recordar.CLAVE)!r}")
    comprobar("Lo guardado es una cadena, no una corrutina",
              isinstance(prefs.datos.get(recordar.CLAVE), str),
              "este era el defecto: antes no se guardaba nada en absoluto")

    perfil = await recuperar_esperando(bucle, prefs)
    comprobar("Al abrir de nuevo se recupera el usuario correcto",
              perfil == {"id_usuario": "u1", "nombre": "Ana",
                         "correo": "ana@cun.edu.co"}, f"{perfil}")
    comprobar("La tabla se consulto con el testigo, no con un objeto suelto",
              repo.consultas == [emitido["testigo"]], f"{repo.consultas}")

    print("\nCuantas consultas cuesta reabrir la aplicacion")

    comprobar("Un testigo nuevo se resuelve con UNA sola consulta",
              len(repo.consultas) == 1 and repo.perfiles_pedidos == [],
              f"{len(repo.consultas)} al testigo, "
              f"{len(repo.perfiles_pedidos)} al perfil "
              "(antes eran dos seguidas, y el telefono esperaba las dos)")

    # Un testigo de los que ya estan en los telefonos del equipo: se emitio
    # antes de que se guardara la copia del perfil.
    viejas = PreferenciasFalsas()
    repo.contador += 100
    antiguo = "testigo-antiguo"
    repo.testigos[antiguo] = {"id": "u1", "perfil": None}
    viejas.datos[recordar.CLAVE] = antiguo
    repo.consultas.clear()
    repo.perfiles_pedidos.clear()

    perfil_viejo = await recuperar_esperando(bucle, viejas)
    comprobar("Un testigo antiguo sigue funcionando",
              perfil_viejo == {"id_usuario": "u1", "nombre": "Ana",
                               "correo": "ana@cun.edu.co"}, f"{perfil_viejo}")
    comprobar("Y para el si se hace la segunda consulta",
              repo.perfiles_pedidos == ["u1"],
              "nadie tiene que volver a ingresar por este cambio")
    repo.consultas.clear()
    repo.perfiles_pedidos.clear()
    repo.consultas.append(emitido["testigo"])

    print("\nCuando no hay sesion que recordar")

    vacias = PreferenciasFalsas()
    comprobar("Dispositivo sin testigo devuelve None",
              await recuperar_esperando(bucle, vacias) is None)
    comprobar("Sin testigo no se consulta la tabla", len(repo.consultas) == 1,
              "consultar con la clave vacia seria gasto puro")

    rotas = PreferenciasFalsas()
    rotas.fallar = True
    comprobar("Si el almacenamiento falla se cae con elegancia a None",
              await recuperar_esperando(bucle, rotas) is None)

    comprobar("Sin bucle de eventos tambien responde, con None",
              await _sin_bucle(prefs),
              "en el navegador el bucle puede no haberse capturado")

    print("\nTestigo caducado frente a fallo de red")

    caducadas = PreferenciasFalsas()
    caducadas.datos[recordar.CLAVE] = "testigo-que-ya-no-existe"
    comprobar("Un testigo caducado devuelve None",
              await recuperar_esperando(bucle, caducadas) is None)
    comprobar("Un testigo caducado SI se borra del dispositivo",
              recordar.CLAVE not in caducadas.datos,
              "si no, se repetiria la misma consulta inutil en cada apertura")

    repo.caer = True
    comprobar("Un fallo de red devuelve None",
              await recuperar_esperando(bucle, prefs) is None)
    comprobar("Un fallo de red NO borra el testigo",
              prefs.datos.get(recordar.CLAVE) == emitido["testigo"],
              "borrarlo haria que un arranque sin cobertura cerrara la sesion "
              "para siempre")
    repo.caer = False

    print("\nCerrar sesion")

    recordar.olvidar(bucle, prefs)
    await asyncio.sleep(0.1)
    comprobar("El testigo desaparece del dispositivo",
              recordar.CLAVE not in prefs.datos)
    comprobar("El testigo queda revocado en la tabla",
              repo.revocados == [emitido["testigo"]], f"{repo.revocados}")

    otro = PreferenciasFalsas()
    otro.datos[recordar.CLAVE] = "testigo-ajeno"
    recordar.olvidar(bucle, otro, revocar=False)
    await asyncio.sleep(0.1)
    comprobar("Con revocar=False se borra del dispositivo pero no de la tabla",
              recordar.CLAVE not in otro.datos
              and "testigo-ajeno" not in repo.revocados)


async def _sin_bucle(prefs):
    hecho = {"llamado": False, "valor": "no llamado"}

    def al_terminar(perfil):
        hecho["llamado"] = True
        hecho["valor"] = perfil

    recordar.recuperar(None, prefs, al_terminar)
    return hecho["llamado"] and hecho["valor"] is None


def main():
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        asyncio.run(principal())
        gc.collect()

    print("\nCorrutinas sin ejecutar")
    sueltas = [str(a.message) for a in avisos
               if "never awaited" in str(a.message)]
    comprobar("Ninguna corrutina queda sin ejecutar",
              not sueltas,
              "\n         ".join(sueltas) if sueltas
              else "era el sintoma exacto del defecto en el registro del "
                   "servicio")

    print("\n" + "=" * 64)
    total, exitosas = len(_resultados), sum(_resultados)
    print(f"Comprobaciones: {exitosas} de {total} superadas.")
    return 0 if exitosas == total else 1


if __name__ == "__main__":
    sys.exit(main())
