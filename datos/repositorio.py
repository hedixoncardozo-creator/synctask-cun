"""Operaciones sobre la tabla unica de SyncTask CUN."""
import uuid
import random
import string
from datetime import datetime, timezone

from datos.cliente import obtener_tabla


def _ahora():
    """Marca de tiempo ISO 8601 en UTC."""
    return datetime.now(timezone.utc).isoformat()


def _generar_codigo():
    """Codigo de acceso de seis caracteres para invitar integrantes (REQ-01)."""
    alfabeto = string.ascii_uppercase + string.digits
    return "".join(random.choices(alfabeto, k=6))


def crear_proyecto(nombre, descripcion, id_administrador):
    """Crea un proyecto y devuelve sus datos, incluido el codigo de acceso."""
    tabla = obtener_tabla()
    id_proyecto = str(uuid.uuid4())
    codigo = _generar_codigo()

    elemento = {
        "PK": f"PROYECTO#{id_proyecto}",
        "SK": "METADATOS",
        "nombre": nombre,
        "descripcion": descripcion,
        "codigo_acceso": codigo,
        "administrador": id_administrador,
        "fecha_creacion": _ahora(),
    }
    tabla.put_item(Item=elemento)
    return elemento