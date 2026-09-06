"""
resumen.py
----------
FASE 2: Envia a Telegram un resumen rapido con el total de partidos
seleccionados para vigilancia hoy.
"""

import json
import datetime
from pathlib import Path

from telegram_utils import enviar_mensaje_telegram

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"


def enviar_resumen():
    if not ARCHIVO_PARTIDOS.exists():
        enviar_mensaje_telegram("Alertas XG\n\nNo hay partidos seleccionados para hoy.")
        return

    try:
        data = json.loads(ARCHIVO_PARTIDOS.read_text(encoding="utf-8"))
    except Exception:
        enviar_mensaje_telegram("Alertas XG\n\nError leyendo partidos del dia.")
        return

    fecha = data.get("fecha", "?")
    partidos = data.get("partidos", [])
    total = len(partidos)

    # Contar por liga
    por_liga = {}
    for p in partidos:
        liga = p.get("liga", "Otra")
        por_liga[liga] = por_liga.get(liga, 0) + 1

    lineas = [
        "Alertas XG",
        f"📊 Resumen del {fecha}",
        "",
        f"Total partidos: {total}",
        "",
    ]

    for liga in sorted(por_liga.keys()):
        lineas.append(f"• {liga}: {por_liga[liga]}")

    msg = "\n".join(lineas)
    enviar_mensaje_telegram(msg)
    print(f"[resumen] Enviado: {total} partidos de {len(por_liga)} ligas.")


if __name__ == "__main__":
    enviar_resumen()
