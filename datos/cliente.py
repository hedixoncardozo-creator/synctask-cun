"""Cliente de DynamoDB para SyncTask.

Las credenciales se obtienen del perfil de instancia IAM (LabInstanceProfile)
a traves de la metadata de EC2 No se almacenan claves en el codigo """


import boto3

REGION = "us-east-1"
NOMBRE_TABLA = "SyncTaskCUN"


def obtener_tabla():
    """Devuelve el recurso Table de DynamoDB."""
    recurso = boto3.resource("dynamodb", region_name=REGION)
    return recurso.Table(NOMBRE_TABLA)