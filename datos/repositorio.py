"""Operaciones sobre la tabla unica de SyncTask CUN."""
import uuid
import random
import string
from datetime import datetime, timezone
from datos.cliente import obtener_tabla, NOMBRE_TABLA

from datos.cliente import obtener_tabla


def _ahora():
    """Marca de tiempo ISO 8601 en UTC."""
    return datetime.now(timezone.utc).isoformat()


def _generar_codigo():
    """Codigo de acceso de seis caracteres para invitar integrantes (REQ-01)."""
    alfabeto = string.ascii_uppercase + string.digits
    return "".join(random.choices(alfabeto, k=6))


def crear_proyecto(nombre, descripcion, id_administrador):
    """Crea un proyecto y su elemento espejo de codigo de acceso (REQ-01).

    Escribe dos elementos en una sola transaccion: los metadatos del proyecto
    y un espejo indexado por codigo, que permite resolver el codigo de acceso
    con una lectura directa en lugar de recorrer la tabla.
    """
    tabla = obtener_tabla()
    cliente = tabla.meta.client
    id_proyecto = str(uuid.uuid4())
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
        ]
    )
    return metadatos


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
    """
    proyecto = buscar_proyecto_por_codigo(codigo)
    if proyecto is None:
        return None

    id_proyecto = proyecto["PK"].split("#", 1)[1]
    tabla = obtener_tabla()
    tabla.put_item(Item={
        "PK": f"PROYECTO#{id_proyecto}",
        "SK": f"MIEMBRO#{id_usuario}",
        "nombre_usuario": nombre_usuario,
        "rol": "integrante",
        "fecha_vinculacion": _ahora(),
    })
    return proyecto





ESTADOS = ("Pendiente", "En progreso", "Terminado")


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
    from boto3.dynamodb.conditions import Key
    tabla = obtener_tabla()
    respuesta = tabla.query(
        KeyConditionExpression=Key("PK").eq(f"PROYECTO#{id_proyecto}")
        & Key("SK").begins_with("TAREA#")
    )
    return respuesta["Items"]
