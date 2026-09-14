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