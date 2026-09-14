"""Prototipo visual del tablero. Datos simulados, sin conexion a DynamoDB."""
import flet as ft

TAREAS = [
    {"titulo": "Aplicar encuesta diagnostica", "responsable": "Luis", "estado": "Pendiente"},
    {"titulo": "Redactar aviso de privacidad", "responsable": "Hedixon", "estado": "Pendiente"},
    {"titulo": "Disenar modelo de datos", "responsable": "Hedixon", "estado": "En progreso"},
    {"titulo": "Configurar servidor EC2", "responsable": "Luis", "estado": "Terminado"},
    {"titulo": "Certificado TLS", "responsable": "Hedixon", "estado": "Terminado"},
]

COLORES = {
    "Pendiente": ft.Colors.ORANGE_100,
    "En progreso": ft.Colors.BLUE_100,
    "Terminado": ft.Colors.GREEN_100,
}


def tarjeta(tarea):
    return ft.Card(
        content=ft.Container(
            content=ft.Column([
                ft.Text(tarea["titulo"], size=14, weight=ft.FontWeight.W_600),
                ft.Row([
                    ft.Icon(ft.Icons.PERSON_OUTLINE, size=14, color=ft.Colors.GREY_600),
                    ft.Text(tarea["responsable"], size=12, color=ft.Colors.GREY_700),
                ], spacing=4),
            ], spacing=6),
            padding=12,
        ),
        elevation=1,
    )


def columna(estado, tareas):
    de_este_estado = [t for t in tareas if t["estado"] == estado]
    return ft.Container(
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Text(estado, size=13, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Text(str(len(de_este_estado)), size=11),
                        bgcolor=ft.Colors.WHITE,
                        border_radius=10,
                        padding=ft.padding.symmetric(horizontal=8, vertical=2),
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                bgcolor=COLORES[estado],
                padding=10,
                border_radius=8,
            ),
            *[tarjeta(t) for t in de_este_estado],
        ], spacing=8),
        width=280,
        padding=8,
    )


def main(page: ft.Page):
    page.title = "SyncTask CUN"
    page.bgcolor = ft.Colors.GREY_50
    page.padding = 0

    page.add(
        ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SYNC, color=ft.Colors.WHITE),
                ft.Text("SyncTask CUN", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            ], spacing=10),
            bgcolor=ft.Colors.GREEN_800,
            padding=16,
        ),
        ft.Container(
            content=ft.Text("Proyecto: Entrega 1 - Idea de proyecto", size=13, color=ft.Colors.GREY_700),
            padding=ft.padding.symmetric(horizontal=16, vertical=10),
        ),
        ft.Row(
            [columna(e, TAREAS) for e in ("Pendiente", "En progreso", "Terminado")],
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
    )

    page.floating_action_button = ft.FloatingActionButton(
        icon=ft.Icons.ADD, bgcolor=ft.Colors.GREEN_800,
    )
    page.update()


ft.run(main, view=ft.AppView.WEB_BROWSER, port=8551, host="0.0.0.0")