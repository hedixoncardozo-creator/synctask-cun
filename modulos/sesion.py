"""Prueba de concepto de sincronizacion entre sesiones (riesgo R-01).

Verifica que el mecanismo de publicacion y suscripcion de Flet atraviesa
el proxy inverso Nginx sobre WebSocket seguro. No usa DynamoDB: su unico
proposito es medir si un cambio hecho en una sesion se refleja en otra y
en cuanto tiempo, segun el criterio de aceptacion de REQ-05 (menos de
tres segundos, sin intervencion del usuario).

Uso:
    sudo systemctl stop synctask
    source ~/venv/bin/activate
    python3 pubsub_demo.py

Luego abrir https://52-54-228-167.nip.io en dos navegadores distintos.
Al terminar: Ctrl+C y sudo systemctl start synctask
"""

import secrets
from datetime import datetime

import flet as ft

ESTADOS = ("Pendiente", "En progreso", "Terminado")

COLORES = {
    "Pendiente": ft.Colors.ORANGE_100,
    "En progreso": ft.Colors.BLUE_100,
    "Terminado": ft.Colors.GREEN_100,
}


def main(page: ft.Page):
    page.title = "Prueba de sincronizacion - SyncTask CUN"
    page.padding = 24
    page.bgcolor = ft.Colors.GREY_50

    # Identificador corto para distinguir cada sesion en la bitacora.
    sesion = secrets.token_hex(2).upper()

    estado_actual = ft.Text(
        ESTADOS[0], size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_900
    )
    marco_estado = ft.Container(
        content=estado_actual,
        bgcolor=COLORES[ESTADOS[0]],
        padding=20,
        border_radius=10,
        alignment=ft.alignment.center,
    )

    bitacora = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=220)

    def registrar(texto, propio):
        bitacora.controls.insert(
            0,
            ft.Text(
                texto,
                size=12,
                color=ft.Colors.GREY_700 if propio else ft.Colors.BLUE_800,
                weight=ft.FontWeight.W_600 if not propio else ft.FontWeight.NORMAL,
            ),
        )
        del bitacora.controls[40:]

    def recibir(mensaje):
        """Se ejecuta en cada sesion suscrita cuando alguien publica."""
        propio = mensaje["sesion"] == sesion
        estado_actual.value = mensaje["estado"]
        marco_estado.bgcolor = COLORES[mensaje["estado"]]
        etiqueta = "esta sesion" if propio else f"sesion {mensaje['sesion']}"
        registrar(
            f"{mensaje['hora']}  {mensaje['estado']}  <- {etiqueta}", propio
        )
        page.update()

    page.pubsub.subscribe(recibir)

    def publicar(e):
        page.pubsub.send_all({
            "estado": e.control.data,
            "sesion": sesion,
            "hora": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        })

    botones = ft.Row(
        [
            ft.ElevatedButton(
                estado,
                data=estado,
                on_click=publicar,
                bgcolor=COLORES[estado],
                color=ft.Colors.BLACK87,
            )
            for estado in ESTADOS
        ],
        spacing=10,
        wrap=True,
    )

    page.add(
        ft.Row(
            [
                ft.Icon(ft.Icons.SYNC, color=ft.Colors.GREEN_800),
                ft.Text(
                    "Prueba de sincronizacion entre sesiones",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                ),
            ],
            spacing=10,
        ),
        ft.Text(
            f"Identificador de esta sesion: {sesion}",
            size=12,
            color=ft.Colors.GREY_600,
        ),
        ft.Divider(),
        ft.Text("Estado compartido de la tarea de prueba", size=13),
        marco_estado,
        ft.Container(height=8),
        botones,
        ft.Container(height=8),
        ft.Text("Bitacora de eventos recibidos", size=13),
        ft.Container(
            content=bitacora,
            bgcolor=ft.Colors.WHITE,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=8,
            padding=12,
        ),
    )

    registrar(f"{datetime.now().strftime('%H:%M:%S')}  sesion iniciada", True)
    page.update()


ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550, host="0.0.0.0")