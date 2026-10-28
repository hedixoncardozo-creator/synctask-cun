"""Operaciones sobre la tabla unica de SyncTask CUN."""
import secrets
import string
import uuid
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from datos.cliente import obtener_tabla, NOMBRE_TABLA

ESTADOS = ("Pendiente", "En progreso", "Terminado")


def _ahora():
    """Marca de tiempo ISO 8601 en UTC."""
    return datetime.now(timezone.utc).isoformat()


def _generar_codigo():
    """Codigo de acceso de seis caracteres para invitar integrantes (REQ-01).

    Se usa secrets y no random porque el codigo es la unica credencial que
    protege el acceso a un proyecto: un generador predecible permitiria
    adivinar codigos ajenos y vulnerar el control de pertenencia (REQ-07).
    """
    alfabeto = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(6))

def crear_proyecto(nombre, descripcion, id_administrador, nombre_administrador):
    """Crea un proyecto, su espejo de codigo y vincula al administrador (REQ-01).

    Escribe tres elementos en una sola transaccion: los metadatos del proyecto,
    un espejo indexado por codigo que permite resolver el codigo de acceso con
    una lectura directa en lugar de recorrer la tabla, y el registro MIEMBRO
    del creador, necesario para que la validacion de pertenencia (REQ-07) lo
    reconozca desde el primer momento.

    Si el codigo generado ya existe, la transaccion se cancela y se reintenta
    con un codigo nuevo, hasta tres veces.
    """
    tabla = obtener_tabla()
    cliente = tabla.meta.client
    id_proyecto = str(uuid.uuid4())

    for intento in range(3):
        codigo = _generar_codigo()
        momento = _ahora()

        metadatos = {
            "PK": f"PROYECTO#{id_proyecto}",
            "SK": "METADATOS",
            "nombre": nombre,
            "descripcion": descripcion,
            "codigo_acceso": codigo,
            "administrador": id_administrador,
            "fecha_creacion": momento,
        }

        espejo = {
            "PK": f"CODIGO#{codigo}",
            "SK": "PROYECTO",
            "id_proyecto": id_proyecto,
            "fecha_creacion": momento,
        }

        miembro = {
            "PK": f"PROYECTO#{id_proyecto}",
            "SK": f"MIEMBRO#{id_administrador}",
            "nombre_usuario": nombre_administrador,
            "rol": "administrador",
            "fecha_vinculacion": momento,
        }

        try:
            cliente.transact_write_items(
                TransactItems=[
                    {"Put": {
                        "TableName": NOMBRE_TABLA,
                        "Item": metadatos,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }},
                    {"Put": {
                        "TableName": NOMBRE_TABLA,
                        "Item": espejo,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }},
                    {"Put": {
                        "TableName": NOMBRE_TABLA,
                        "Item": miembro,
                    }},
                ]
            )
            return metadatos
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if intento == 2:
                raise RuntimeError(
                    "No se pudo generar un codigo de acceso unico en tres intentos."
                )


def buscar_proyecto_por_codigo(codigo):
    """Resuelve un codigo de acceso a los metadatos del proyecto (REQ-02).

    Usa el elemento espejo CODIGO#{codigo} para obtener el id del proyecto
    con una lectura directa, y luego recupera sus metadatos. Dos get_item
    en lugar de recorrer la tabla.
    """
    tabla = obtener_tabla()

    espejo = tabla.get_item(Key={"PK": f"CODIGO#{codigo.upper()}", "SK": "PROYECTO"})
    if "Item" not in espejo:
        return None

    id_proyecto = espejo["Item"]["id_proyecto"]
    proyecto = tabla.get_item(Key={"PK": f"PROYECTO#{id_proyecto}", "SK": "METADATOS"})
    return proyecto.get("Item")


def unirse_a_proyecto(codigo, id_usuario, nombre_usuario):
    """Registra a un usuario como integrante de un proyecto (REQ-02).

    Devuelve los metadatos del proyecto, o None si el codigo no existe.
    Si el usuario ya pertenece al proyecto la operacion no lo sobrescribe,
    para no perder la fecha de vinculacion original ni degradar el rol de
    un administrador que vuelva a introducir el codigo.
    """
    proyecto = buscar_proyecto_por_codigo(codigo)
    if proyecto is None:
        return None

    id_proyecto = proyecto["PK"].split("#", 1)[1]
    tabla = obtener_tabla()

    try:
        tabla.put_item(
            Item={
                "PK": f"PROYECTO#{id_proyecto}",
                "SK": f"MIEMBRO#{id_usuario}",
                "nombre_usuario": nombre_usuario,
                "rol": "integrante",
                "fecha_vinculacion": _ahora(),
            },
            ConditionExpression="attribute_not_exists(SK)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Ya era integrante: se conserva el registro existente.

    return proyecto


def es_miembro(id_proyecto, id_usuario):
    """Indica si un usuario pertenece al proyecto (REQ-07).

    Se consulta antes de cada operacion de escritura y antes de entregar el
    tablero, de modo que un usuario ajeno no pueda leer ni modificar tareas
    de un proyecto que no es suyo.
    """
    tabla = obtener_tabla()
    respuesta = tabla.get_item(
        Key={"PK": f"PROYECTO#{id_proyecto}", "SK": f"MIEMBRO#{id_usuario}"}
    )
    return "Item" in respuesta


def listar_integrantes(id_proyecto):
    """Devuelve los integrantes de un proyecto en una sola consulta."""
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"PROYECTO#{id_proyecto}")
        & Key("SK").begins_with("MIEMBRO#")
    )
    return respuesta["Items"]


# tareas

def crear_tarea(id_proyecto, titulo, descripcion, responsable, fecha_limite):
    """Crea una tarea en estado Pendiente (REQ-03)."""
    tabla = obtener_tabla()
    id_tarea = str(uuid.uuid4())
    momento = _ahora()

    elemento = {
        "PK": f"PROYECTO#{id_proyecto}",
        "SK": f"TAREA#{id_tarea}",
        "titulo": titulo,
        "descripcion": descripcion,
        "estado": "Pendiente",
        "responsable": responsable,
        "fecha_limite": fecha_limite,
        "fecha_creacion": momento,
        "fecha_actualizacion": momento,
    }
    tabla.put_item(Item=elemento)
    return elemento


def cambiar_estado(id_proyecto, id_tarea, nuevo_estado):
    """Cambia el estado de una tarea y registra la fecha del cambio (REQ-04).

    Lanza ValueError si el estado no es uno de los tres admitidos.
    """
    if nuevo_estado not in ESTADOS:
        raise ValueError(f"Estado invalido: {nuevo_estado}. Admitidos: {ESTADOS}")

    tabla = obtener_tabla()
    respuesta = tabla.update_item(
        Key={"PK": f"PROYECTO#{id_proyecto}", "SK": f"TAREA#{id_tarea}"},
        UpdateExpression="SET estado = :e, fecha_actualizacion = :f",
        ExpressionAttributeValues={":e": nuevo_estado, ":f": _ahora()},
        ConditionExpression="attribute_exists(PK)",
        ReturnValues="ALL_NEW",
    )
    return respuesta["Attributes"]


def listar_tareas(id_proyecto):
    """Devuelve todas las tareas de un proyecto en una sola consulta."""
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"PROYECTO#{id_proyecto}")
        & Key("SK").begins_with("TAREA#")
    )
    return respuesta["Items"]




# -------------------------------------------------------------- comentarios


def agregar_comentario(id_proyecto, id_tarea, id_autor, nombre_autor, texto):
    """Agrega un comentario de texto plano a una tarea (REQ-06).

    No existe operacion de edicion ni de borrado: el historial se conserva tal
    como se escribio, que es lo que permite evidenciar el aporte individual.
    """
    if not texto or not texto.strip():
        raise ValueError("El comentario no puede estar vacio.")

    tabla = obtener_tabla()
    momento = _ahora()
    sufijo = secrets.token_hex(2)

    elemento = {
        "PK": f"PROYECTO#{id_proyecto}",
        "SK": f"COMENTARIO#{id_tarea}#{momento}#{sufijo}",
        "id_tarea": id_tarea,
        "id_autor": id_autor,
        "nombre_autor": nombre_autor,
        "texto": texto.strip(),
        "fecha": momento,
    }
    tabla.put_item(Item=elemento)
    return elemento


def listar_comentarios(id_proyecto, id_tarea):
    """Historial de una tarea en orden cronologico (REQ-06).

    El orden lo garantiza la clave de ordenamiento; no se ordena en memoria.
    """
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"PROYECTO#{id_proyecto}")
        & Key("SK").begins_with(f"COMENTARIO#{id_tarea}#")
    )
    return respuesta["Items"]


def listar_comentarios_del_proyecto(id_proyecto):
    """Todos los comentarios de un proyecto, en una sola consulta.

    Sirve para calcular el numero de comentarios por tarea sin ejecutar una
    consulta por cada tarjeta del tablero.
    """
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"PROYECTO#{id_proyecto}")
        & Key("SK").begins_with("COMENTARIO#")
    )
    return respuesta["Items"]


def registrar_proyecto_de_usuario(id_usuario, id_proyecto, nombre_proyecto, rol):
    """Anade el proyecto al indice de lectura del usuario.

    Es idempotente a proposito: repetir la llamada con los mismos datos deja
    el elemento igual, lo que permite usarla para reparar un espejo perdido.
    """
    tabla = obtener_tabla()
    elemento = {
        "PK": f"USUARIO#{id_usuario}",
        "SK": f"PROYECTO#{id_proyecto}",
        "id_proyecto": id_proyecto,
        "nombre_proyecto": nombre_proyecto,
        "rol": rol,
        "fecha_vinculacion": _ahora(),
    }
    tabla.put_item(Item=elemento)
    return elemento


def listar_proyectos_de_usuario(id_usuario):
    """Proyectos a los que pertenece el usuario, en una sola consulta."""
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"USUARIO#{id_usuario}")
        & Key("SK").begins_with("PROYECTO#")
    )
    return respuesta["Items"]


def obtener_proyecto(id_proyecto):
    """Metadatos de un proyecto, o None si no existe.

    Se usa al entrar al tablero para mostrar el nombre y el codigo de acceso
    sin depender de lo que trajera la pantalla anterior.
    """
    tabla = obtener_tabla()
    respuesta = tabla.get_item(
        Key={"PK": f"PROYECTO#{id_proyecto}", "SK": "METADATOS"}
    )
    return respuesta.get("Item")


class ErrorDeAutoria(Exception):
    """Se intento modificar un comentario ajeno, o ya suprimido."""


def editar_comentario(id_proyecto, clave_orden, id_autor, texto):
    """Reemplaza el texto de un comentario propio (REQ-06).

    Deja constancia de la edicion con fecha_edicion: un comentario editado no
    debe poder hacerse pasar por el original.
    """
    if not texto or not texto.strip():
        raise ValueError("El comentario no puede quedar vacio.")

    tabla = obtener_tabla()
    try:
        respuesta = tabla.update_item(
            Key={"PK": f"PROYECTO#{id_proyecto}", "SK": clave_orden},
            UpdateExpression="SET texto = :texto, fecha_edicion = :ahora",
            ConditionExpression=(
                "id_autor = :autor AND attribute_not_exists(eliminado)"
            ),
            ExpressionAttributeValues={
                ":texto": texto.strip(),
                ":ahora": _ahora(),
                ":autor": id_autor,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ErrorDeAutoria(
                "Solo el autor puede editar su comentario, y no si ya fue eliminado."
            ) from error
        raise
    return respuesta["Attributes"]


def eliminar_comentario(id_proyecto, clave_orden, id_autor):
    """Marca como eliminado un comentario propio, conservando la huella.

    No se usa delete_item: el elemento sobrevive sin texto, de modo que el
    hilo conserva su forma y queda registro de que hubo una participacion.
    """
    tabla = obtener_tabla()
    try:
        respuesta = tabla.update_item(
            Key={"PK": f"PROYECTO#{id_proyecto}", "SK": clave_orden},
            UpdateExpression=(
                "SET eliminado = :si, texto = :vacio, fecha_eliminacion = :ahora"
            ),
            ConditionExpression=(
                "id_autor = :autor AND attribute_not_exists(eliminado)"
            ),
            ExpressionAttributeValues={
                ":si": True,
                ":vacio": "",
                ":ahora": _ahora(),
                ":autor": id_autor,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ErrorDeAutoria(
                "Solo el autor puede eliminar su comentario, y solo una vez."
            ) from error
        raise
    return respuesta["Attributes"]


from datetime import timedelta  # noqa: E402  (va con el resto de imports)

# --------------------------------------------- edicion y supresion de tareas

# Las tareas se suprimen igual que los comentarios: con una marca, no con
# delete_item. La razon es distinta, eso si. En un comentario lo que se protege
# es la constancia del aporte individual; en una tarea, lo que se protege es la
# conversacion que cuelga de ella. Un delete_item dejaria los comentarios de esa
# tarea convertidos en elementos huerfanos: seguirian ocupando la particion del
# proyecto, los contaria contar_por_tarea, y nadie podria llegar a ellos para
# entender de que hablaban. Marcar la tarea mantiene el arbol entero coherente
# con una sola escritura.


def editar_tarea(id_proyecto, id_tarea, titulo, descripcion, responsable,
                 fecha_limite):
    """Actualiza los campos editables de una tarea vigente (REQ-03).

    No admite cambiar el estado: para eso esta cambiar_estado(), que ademas
    difunde el evento correspondiente. Mezclar ambas cosas en una sola funcion
    haria que un error de escritura en el titulo moviera la tarea de columna.
    """
    if not titulo or not titulo.strip():
        raise ValueError("La tarea necesita un titulo.")

    tabla = obtener_tabla()
    try:
        respuesta = tabla.update_item(
            Key={"PK": f"PROYECTO#{id_proyecto}", "SK": f"TAREA#{id_tarea}"},
            UpdateExpression=(
                "SET titulo = :titulo, descripcion = :desc, "
                "responsable = :resp, fecha_limite = :fecha, "
                "fecha_edicion = :ahora"
            ),
            ConditionExpression=(
                "attribute_exists(PK) AND attribute_not_exists(eliminada)"
            ),
            ExpressionAttributeValues={
                ":titulo": titulo.strip(),
                ":desc": (descripcion or "").strip(),
                ":resp": (responsable or "").strip(),
                ":fecha": (fecha_limite or "").strip(),
                ":ahora": _ahora(),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError("Esa tarea ya no existe o fue eliminada.") from error
        raise
    return respuesta["Attributes"]


def eliminar_tarea(id_proyecto, id_tarea):
    """Marca una tarea como eliminada, conservando su hilo de comentarios."""
    tabla = obtener_tabla()
    try:
        respuesta = tabla.update_item(
            Key={"PK": f"PROYECTO#{id_proyecto}", "SK": f"TAREA#{id_tarea}"},
            UpdateExpression="SET eliminada = :si, fecha_eliminacion = :ahora",
            ConditionExpression=(
                "attribute_exists(PK) AND attribute_not_exists(eliminada)"
            ),
            ExpressionAttributeValues={":si": True, ":ahora": _ahora()},
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError("Esa tarea ya fue eliminada.") from error
        raise
    return respuesta["Attributes"]


# ------------------------------------------------------- sesiones recordadas

# El objetivo es que al abrir el APK no haya que escribir correo y contrasena
# cada vez. La tentacion facil seria guardar en el telefono el identificador
# del usuario y confiar en el al arrancar; eso seria un agujero evidente, ya
# que cualquiera que editara ese valor pasaria a ser otra persona.
#
# En su lugar se emite un testigo: una cadena aleatoria de 256 bits que no
# significa nada por si misma, se guarda en el telefono, y cuya correspondencia
# con un usuario vive solo en DynamoDB. Adivinarlo no es viable y falsificarlo
# tampoco, porque no se deriva de ningun dato del usuario. Caduca a los treinta
# dias y se revoca al cerrar sesion.
#
# Queda declarado un limite: el testigo se guarda sin cifrar en el
# almacenamiento de la aplicacion. En Android ese almacenamiento es privado de
# la aplicacion, pero un telefono con acceso de superusuario podria leerlo. La
# mitigacion real seria el almacen de credenciales del sistema, que excede el
# alcance de esta entrega.

DIAS_DE_VIGENCIA = 30


def crear_testigo(id_usuario, perfil=None):
    """Emite un testigo de sesion y devuelve su cadena.

    Si se pasa `perfil`, se guarda junto al testigo una copia del nombre y el
    correo. No es un capricho de normalizacion: al reabrir la aplicacion hay
    que traducir el testigo a un usuario, y sin esa copia hacen falta DOS
    consultas seguidas a DynamoDB —una para el testigo y otra para el perfil—
    que el telefono espera antes de poder pintar nada. Con la copia basta una.

    Es la misma tecnica de desnormalizacion que ya usa este modulo para el
    indice de proyectos por usuario. Lo copiado es poco y cambia poco: nombre
    y correo. Si el usuario se cambia el nombre, los testigos ya emitidos
    mostraran el anterior hasta que vuelva a ingresar, que es un precio
    razonable por quitar una espera en cada apertura.
    """
    testigo = secrets.token_urlsafe(32)
    momento = datetime.now(timezone.utc)
    caduca = momento + timedelta(days=DIAS_DE_VIGENCIA)

    elemento = {
        "PK": f"TESTIGO#{testigo}",
        "SK": "SESION",
        "id_usuario": id_usuario,
        "fecha_emision": momento.isoformat(),
        "fecha_caducidad": caduca.isoformat(),
        # Segundos desde la epoca, para poder activar mas adelante la
        # expiracion automatica de DynamoDB sobre este atributo y que la
        # tabla se limpie sola sin un proceso de mantenimiento.
        "caduca_epoch": int(caduca.timestamp()),
    }
    if perfil:
        elemento["nombre_cache"] = perfil.get("nombre", "")
        elemento["correo_cache"] = perfil.get("correo", "")

    obtener_tabla().put_item(Item=elemento)
    return testigo


def _testigo_vigente(testigo):
    """Lee el elemento del testigo si existe y no ha caducado.

    La caducidad se comprueba al usarlo y no se confia en que la expiracion
    automatica de la tabla ya lo haya retirado: esa expiracion no garantiza un
    instante exacto de borrado.
    """
    if not testigo:
        return None
    respuesta = obtener_tabla().get_item(Key={"PK": f"TESTIGO#{testigo}",
                                              "SK": "SESION"})
    elemento = respuesta.get("Item")
    if not elemento:
        return None
    try:
        caduca = datetime.fromisoformat(elemento["fecha_caducidad"])
    except (KeyError, ValueError):
        return None
    if caduca <= datetime.now(timezone.utc):
        return None
    return elemento


def usuario_de_testigo(testigo):
    """Devuelve el identificador de usuario del testigo, o None."""
    elemento = _testigo_vigente(testigo)
    return elemento.get("id_usuario") if elemento else None


def sesion_de_testigo(testigo):
    """Traduce un testigo a un perfil en UNA sola consulta.

    Devuelve `(id_usuario, perfil)`. El perfil puede venir como None cuando el
    testigo se emitio antes de que se guardara la copia —los que ya estan en
    los telefonos del equipo—; en ese caso quien llama completa con
    `obtener_usuario`, que es el camino de dos consultas de siempre. Asi el
    cambio no obliga a nadie a volver a ingresar.
    """
    elemento = _testigo_vigente(testigo)
    if not elemento:
        return None, None

    id_usuario = elemento.get("id_usuario")
    if not id_usuario:
        return None, None

    if "nombre_cache" not in elemento:
        return id_usuario, None

    return id_usuario, {
        "id_usuario": id_usuario,
        "nombre": elemento.get("nombre_cache", ""),
        "correo": elemento.get("correo_cache", ""),
    }


def revocar_testigo(testigo):
    """Invalida un testigo. Se invoca al cerrar sesion."""
    if not testigo:
        return
    obtener_tabla().delete_item(Key={"PK": f"TESTIGO#{testigo}", "SK": "SESION"})


def obtener_usuario(id_usuario):
    """Perfil publico de un usuario a partir de su identificador.

    Devuelve None si no existe. Nunca incluye la clave derivada ni la sal: se
    limpian aqui para que ningun camino de la aplicacion pueda llevarlas a la
    pantalla por descuido.
    """
    respuesta = obtener_tabla().get_item(
        Key={"PK": f"USUARIO#{id_usuario}", "SK": "PERFIL"}
    )
    elemento = respuesta.get("Item")
    if not elemento:
        return None
    return {
        "id_usuario": id_usuario,
        "nombre": elemento.get("nombre", ""),
        "correo": elemento.get("correo", ""),
    }
