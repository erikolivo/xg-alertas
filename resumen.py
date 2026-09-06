"""
resumen.py
----------
FASE 2: Envia a Telegram un resumen de los partidos seleccionados
antes de que empiecen.
"""

import json
import datetime
from pathlib import Path

from telegram_utils import enviar_mensaje_telegram, escapar_html
from estado_diario import ya_se_hizo, marcar_hecho
from fetch_data import extraer_forma_equipo

ARCHIVO = Path(__file__).parent / "data" / "partidos_hoy.json"
ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))

CORONA_FAVORITO = "\U0001F451"


def _hora_display(hora_str):
    """Formatea la hora para el mensaje."""
    if not hora_str:
        return "?"
    return hora_str


def _forma_display(forma):
    """Muestra la forma reciente como W/D/L."""
    if not forma:
        return ""
    ultimos = forma[:5]
    partes = []
    for f in ultimos:
        r = f.get("resultado", "")
        if r == "w":
            partes.append("W")
        elif r == "d":
            partes.append("D")
        elif r == "l":
            partes.append("L")
    return "".join(partes)


def _construir_mensaje_resumen(partido):
    """Construye el mensaje de Telegram para un partido."""
    local = partido["local"]
    visitante = partido["visitante"]
    hora = _hora_display(partido.get("hora", ""))
    liga = partido.get("liga", "")
    fav = partido.get("favorito", "")
    es_local = partido.get("favorito_es_local")

    # Emoji del favorito
    corona = ""
    if fav:
        corona = f" {CORONA_FAVORITO}"

    # Forma reciente
    home_id = partido.get("home_id", "")
    away_id = partido.get("away_id", "")
    forma_home = extraer_forma_equipo(home_id) if home_id else []
    forma_away = extraer_forma_equipo(away_id) if away_id else []
    forma_h_str = _forma_display(forma_home)
    forma_a_str = _forma_display(forma_away)

    lineas = [
        f"<b>{escapar_html(liga)}</b>",
        f"",
        f"{escapar_html(local)} vs {escapar_html(visitante)}",
        f"Hora: {hora}",
        f"",
    ]

    if fav:
        lado = "Local" if es_local else "Visitante"
        lineas.append(f"Favorito: {escapar_html(fav)} ({lado}){corona}")
        lineas.append(f"")

    if forma_h_str or forma_a_str:
        lineas.append(f"Forma reciente:")
        if forma_h_str:
            lineas.append(f"  {escapar_html(local)}: {forma_h_str}")
        if forma_a_str:
            lineas.append(f"  {escapar_html(visitante)}: {forma_a_str}")
        lineas.append(f"")

    lineas.append(f"Alertas de xG activas durante el partido.")

    return "\n".join(lineas)


def enviar_resumen():
    """Ejecuta la Fase 2: envia resumen por Telegram."""
    if ya_se_hizo("resumen"):
        print("[resumen] Ya se envio el resumen de hoy.")
        return False

    if not ARCHIVO.exists():
        print("[resumen] No existe partidos_hoy.json. Correr fase 1 primero.")
        return False

    data = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    partidos = data.get("partidos", [])
    if not partidos:
        print("[resumen] No hay partidos para enviar resumen.")
        return False

    fecha = data.get("fecha", "")
    header = f"<b>⚽ Partidos de hoy — {fecha}</b>\n"
    header += f"{len(partidos)} partido(s) en seguimiento\n"
    header += f"━━━━━━━━━━━━━━━"

    mensajes = [header]
    for p in partidos:
        msg = _construir_mensaje_resumen(p)
        mensajes.append(f"\n{msg}")

    texto_completo = "\n".join(mensajes)

    print(f"[resumen] Enviando resumen de {len(partidos)} partido(s)...")
    exito = enviar_mensaje_telegram(texto_completo)

    if exito:
        marcar_hecho("resumen")
        print("[resumen] Resumen enviado exitosamente.")
    else:
        print("[resumen] Error al enviar resumen.")

    return exito


if __name__ == "__main__":
    enviar_resumen()
