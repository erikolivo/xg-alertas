"""
reporte_diario.py
-----------------
Envia a Telegram el reporte de las 6am con resultados y acierto
por tipo de alerta.
"""

import json
import datetime
from pathlib import Path

from telegram_utils import enviar_mensaje_telegram, escapar_html
from estado_diario import ya_se_hizo, marcar_hecho

DATA_DIR = Path(__file__).parent / "data"
DIR_HISTORIAL_DIAS = DATA_DIR / "historial_dias"


def _cargar_historial(fecha):
    """Carga el historial de un dia especifico."""
    archivo = DIR_HISTORIAL_DIAS / f"{fecha}.json"
    if not archivo.exists():
        return []
    try:
        return json.loads(archivo.read_text(encoding="utf-8"))
    except Exception:
        return []


def _calcular_estadisticas(partidos):
    """Calcula estadisticas de acierto por tipo de alerta."""
    stats = {}
    for p in partidos:
        for a in p.get("alertas_enviadas", []):
            tipo = a.get("tipo", "desconocido")
            if tipo not in stats:
                stats[tipo] = {"total": 0, "aciertos": 0}
            stats[tipo]["total"] += 1
            if a.get("acierto"):
                stats[tipo]["aciertos"] += 1
    return stats


def _emoji_tipo(tipo):
    """Devuelve el emoji para cada tipo de alerta."""
    mapa = {
        "dominancia_xg": "🟠",
        "no_fav_domina": "🔴",
        "xg_vs_marcador": "🎯",
        "cierre": "⏰",
        "ampliacion": "🔵",
    }
    return mapa.get(tipo, "❓")


def _nombre_tipo(tipo):
    """Nombre legible para cada tipo de alerta."""
    mapa = {
        "dominancia_xg": "Dominancia xG",
        "no_fav_domina": "No favorito domina",
        "xg_vs_marcador": "xG vs Marcador",
        "cierre": "Cierre",
        "ampliacion": "Ampliación",
    }
    return mapa.get(tipo, tipo)


def generar_reporte(fecha=None):
    """Genera y envia el reporte diario por Telegram."""
    if ya_se_hizo("reporte"):
        print("[reporte] Ya se envio el reporte de hoy.")
        return

    if fecha is None:
        ayer = datetime.date.today() - datetime.timedelta(days=1)
        fecha = ayer.isoformat()

    partidos = _cargar_historial(fecha)
    if not partidos:
        print(f"[reporte] No hay historial para {fecha}.")
        return

    stats = _calcular_estadisticas(partidos)
    total_alertas = sum(s["total"] for s in stats.values())
    total_aciertos = sum(s["aciertos"] for s in stats.values())
    pct_total = round(total_aciertos / total_alertas * 100, 1) if total_alertas > 0 else 0

    lineas = [
        f"<b>📊 Reporte diario — {fecha}</b>",
        f"",
        f"Partidos: {len(partidos)}",
        f"Total alertas: {total_alertas}",
        f"Aciertos: {total_aciertos} ({pct_total}%)",
        f"",
        f"<b>Por tipo de alerta:</b>",
    ]

    for tipo, s in sorted(stats.items()):
        emoji = _emoji_tipo(tipo)
        nombre = _nombre_tipo(tipo)
        pct = round(s["aciertos"] / s["total"] * 100, 1) if s["total"] > 0 else 0
        barra = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        lineas.append(f"  {emoji} {nombre}: {s['aciertos']}/{s['total']} ({pct}%) {barra}")

    # Detalle de partidos
    lineas.append("")
    lineas.append("<b>Detalle:</b>")
    for p in partidos:
        resultado = p.get("resultado_final", {})
        goles_h = resultado.get("goles_home", "?")
        goles_a = resultado.get("goles_away", "?")
        alertas = p.get("alertas_enviadas", [])
        aciertos = sum(1 for a in alertas if a.get("acierto"))

        xg_h = None
        xg_a = None
        snapshots = p.get("snapshots", [])
        if snapshots:
            xg_h = snapshots[-1].get("xg_home")
            xg_a = snapshots[-1].get("xg_away")

        xg_str = ""
        if xg_h is not None and xg_a is not None:
            xg_str = f" (xG: {xg_h:.2f} - {xg_a:.2f})"

        lineas.append(
            f"  {escapar_html(p.get('local', '?'))} {goles_h} - {goles_a} "
            f"{escapar_html(p.get('visitante', '?'))}{xg_str}"
        )
        if alertas:
            lineas.append(f"    Alertas: {len(alertas)}, Aciertos: {aciertos}")

    texto = "\n".join(lineas)

    print(f"[reporte] Enviando reporte del {fecha}...")
    exito = enviar_mensaje_telegram(texto)

    if exito:
        marcar_hecho("reporte")
        print("[reporte] Reporte enviado.")
    else:
        print("[reporte] Error al enviar reporte.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="Fecha YYYY-MM-DD (por defecto, ayer)")
    args = parser.parse_args()
    generar_reporte(fecha=args.fecha)
