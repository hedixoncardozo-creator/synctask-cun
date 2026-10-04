"""Alta de proyectos y vinculacion por codigo (REQ-02).

Este modulo existe por una razon concreta: cada operacion sobre un proyecto
toca dos elementos de la tabla —el registro autoritativo y el indice de
lectura del usuario— y conviene que ese par se escriba siempre desde un solo
sitio. Si cada pantalla lo hiciera por su cuenta, tarde o temprano una de
ellas olvidaria el indice y el proyecto desapareceria de la lista del usuario
sin que nadie entendiera por que.

El orden es deliberado: primero el registro autoritativo, despues el espejo.
Al reves, un fallo intermedio dejaria al usuario viendo en su lista un
proyecto al que no pertenece, que es el peor de los dos estados posibles.
"""

from botocore.exceptions import ClientError

from datos import repositorio


class ErrorDeCodigo(Exception):
    """El codigo de acceso no corresponde a ningun proyecto vigente."""


def _id_de(proyecto):
    return proyecto["PK"].split("#", 1)[1]


def crear(nombre, descripcion, usuario):
    """Crea un proyecto y deja al autor registrado como administrador.

    Devuelve los metadatos del proyecto, que incluyen el codigo de acceso que
    debe compartir con sus companeros.
    """
    if not nombre or not nombre.strip():
        raise ValueError("El proyecto necesita un nombre.")

    proyecto = repositorio.crear_proyecto(
        nombre=nombre.strip(),
        descripcion=(descripcion or "").strip(),
        id_administrador=usuario["id_usuario"],
        nombre_administrador=usuario["nombre"],
    )
    repositorio.registrar_proyecto_de_usuario(
        id_usuario=usuario["id_usuario"],
        id_proyecto=_id_de(proyecto),
        nombre_proyecto=proyecto.get("nombre", nombre.strip()),
        rol="administrador",
    )
    return proyecto


def unirse(codigo, usuario):
    """Vincula al usuario a un proyecto a partir de su codigo (REQ-02).

    Se busca el proyecto antes de escribir nada, de modo que un codigo
    equivocado produzca un mensaje claro en lugar de una excepcion de la capa
    de datos. Volver a unirse a un proyecto del que ya se es miembro no es un
    error: se trata como exito y se repara el indice de lectura, que es
    exactamente lo que el usuario espera cuando reintroduce un codigo.
    """
    codigo = (codigo or "").strip().upper()
    if not codigo:
        raise ErrorDeCodigo("Escriba el codigo que le compartieron.")

    proyecto = repositorio.buscar_proyecto_por_codigo(codigo)
    if not proyecto:
        raise ErrorDeCodigo("Ese codigo no corresponde a ningun proyecto.")

    id_proyecto = _id_de(proyecto)
    try:
        repositorio.unirse_a_proyecto(codigo, usuario["id_usuario"], usuario["nombre"])
    except ClientError as error:
        codigo_de_error = error.response["Error"]["Code"]
        esperados = ("ConditionalCheckFailedException", "TransactionCanceledException")
        ya_era_miembro = codigo_de_error in esperados and repositorio.es_miembro(
            id_proyecto, usuario["id_usuario"]
        )
        if not ya_era_miembro:
            raise

    repositorio.registrar_proyecto_de_usuario(
        id_usuario=usuario["id_usuario"],
        id_proyecto=id_proyecto,
        nombre_proyecto=proyecto.get("nombre", "Proyecto"),
        rol="integrante",
    )
    return proyecto


def listar_mios(usuario):
    """Proyectos del usuario, los mas recientes primero."""
    elementos = repositorio.listar_proyectos_de_usuario(usuario["id_usuario"])
    return sorted(
        elementos, key=lambda e: e.get("fecha_vinculacion", ""), reverse=True
    )


def obtener(id_proyecto, usuario):
    """Metadatos del proyecto, solo si el usuario pertenece a el (REQ-07).

    La comprobacion se hace aqui y no en la pantalla: una pantalla se puede
    alcanzar escribiendo la direccion a mano, de modo que la autorizacion no
    puede depender de por donde haya navegado el usuario.
    """
    from modulos.tareas import _exigir_pertenencia

    _exigir_pertenencia(id_proyecto, usuario["id_usuario"])
    return repositorio.obtener_proyecto(id_proyecto)