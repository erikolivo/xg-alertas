"""
cerrar_resultados.py
---------------------
FASE 4 — Obtiene resultados finales, audita alertas y archiva el dia.
"""

import json
import datetime
from pathlib import Path

from fetch_data import obtener_resultado_final, obtener_detalles_partido, extraer_xg
from estado_diario import ya_se_hizo, marcar_hecho
from telegram_utils import enviar_mensaje_telegram, escapar_html

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"
DIR_HISTORIAL_DIAS = DATA_DIR / "historial_dias"
ARCHIVO_EXCEL = DATA_DIR / "estadisticas.xlsx"

VENTANA_ACIERTO_MINUTOS = 15


def calcular_acierto(alerta, goles_local, goles_visitante, favorito_es_local):
    """
    Determina si una alerta fue acertada.
    Criterio: el equipo que dominaba en xG debio anotar o mantener ventaja.
    """
    tipo = alerta.get("tipo", "")

    if tipo == "dominancia_xg":
        # Acierto si el equipo dominante eventualmente anota o gana
        if favorito_es_local:
            return goles_local >= goles_visitante
        else:
            return goles_visitante >= goles_local

    elif tipo == "no_fav_domina":
        # Acierto si el no-favorito anota o empata
        if favorito_es_local:
            return goles_visitante >= goles_local
        else:
            return goles_local >= goles_visitante

    elif tipo == "xg_vs_marcador":
        # Acierto si el equipo con mejor xG termina ganando
        # (la "justicia" estadistica se cumple)
        if favorito_es_local:
            return goles_local > goles_visitante
        else:
            return goles_visitante > goles_local

    elif tipo == "cierre":
        # Acierto si el equipo dominante marca en los ultimos minutos
        if favorito_es_local:
            return goles_local > goles_visitante
        else:
            return goles_visitante > goles_local

    elif tipo == "ampliacion":
        # Acierto si el favorito efectivamente amplia o mantiene
        if favorito_es_local:
            return goles_local > goles_visitante
        else:
            return goles_visitante > goles_local

    return None


def _guardar_historial_dia(fecha, partidos):
    """Guarda el historial del dia en un archivo JSON."""
    DIR_HISTORIAL_DIAS.mkdir(parents=True, exist_ok=True)
    archivo = DIR_HISTORIAL_DIAS / f"{fecha}.json"

    registros = []
    for p in partidos:
        resultado = p.get("resultado_final", {})
        xg_final_home = None
        xg_final_away = None

        # Obtener xG final de los snapshots
        snapshots = p.get("snapshots", [])
        if snapshots:
            ultimo = snapshots[-1]
            xg_final_home = ultimo.get("xg_home")
            xg_final_away = ultimo.get("xg_away")

        registro = {
            "fixture_id": p.get("fixture_id"),
            "fecha": fecha,
            "liga": p.get("liga"),
            "local": p.get("local"),
            "visitante": p.get("visitante"),
            "goles_local": resultado.get("goles_home"),
            "goles_visitante": resultado.get("goles_away"),
            "xg_final_home": xg_final_home,
            "xg_final_away": xg_final_away,
            "favorito": p.get("favorito"),
            "favorito_es_local": p.get("favorito_es_local"),
            "alertas_enviadas": p.get("alertas_enviadas", []),
            "total_alertas": len(p.get("alertas_enviadas", [])),
        }
        registros.append(registro)

    # Guardar (append si ya existe)
    existentes = []
    if archivo.exists():
        try:
            existentes = json.loads(archivo.read_text(encoding="utf-8"))
        except Exception:
            existentes = []

    existentes.extend(registros)
    archivo.write_text(json.dumps(existentes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cierre] Historial guardado: {archivo}")


def _actualizar_excel(fecha, partidos):
    """Actualiza el archivo Excel con resultados y aciertos."""
    try:
        from openpyxl import Workbook, load_workbook
    except ImportError:
        print("[cierre] openpyxl no instalado, saltando Excel.")
        return

    DATA_DIR.mkdir(exist_ok=True)

    if ARCHIVO_EXCEL.exists():
        wb = load_workbook(ARCHIVO_EXCEL)
    else:
        wb = Workbook()

    # Hoja de resultados
    if "Resultados" in wb.sheetnames:
        ws = wb["Resultados"]
    else:
        ws = wb.active
        ws.title = "Resultados"
        ws.append(["Fecha", "Liga", "Local", "Visitante", "Goles", "xG Local", "xG Visitante",
                    "Favorito", "Alertas", "Aciertos"])

    for p in partidos:
        resultado = p.get("resultado_final", {})
        goles_h = resultado.get("goles_home")
        goles_a = resultado.get("goles_away")
        goles_str = f"{goles_h} - {goles_a}" if goles_h is not None else "?"

        snapshots = p.get("snapshots", [])
        xg_h = snapshots[-1].get("xg_home") if snapshots else None
        xg_a = snapshots[-1].get("xg_away") if snapshots else None

        alertas = p.get("alertas_enviadas", [])
        total_alertas = len(alertas)
        aciertos = sum(1 for a in alertas if a.get("acierto"))

        ws.append([
            fecha, p.get("liga", ""), p.get("local", ""), p.get("visitante", ""),
            goles_str, xg_h, xg_a,
            p.get("favorito", ""), total_alertas, aciertos,
        ])

    # Hoja de resumen diario
    if "Resumen" in wb.sheetnames:
        ws_res = wb["Resumen"]
    else:
        ws_res = wb.create_sheet("Resumen")
        ws_res.append(["Fecha", "Partidos", "Total Alertas", "Aciertos", "% Acierto"])

    total_alertas_dia = sum(len(p.get("alertas_enviadas", [])) for p in partidos)
    aciertos_dia = sum(
        1 for p in partidos
        for a in p.get("alertas_enviadas", [])
        if a.get("acierto")
    )
    pct = round(aciertos_dia / total_alertas_dia * 100, 1) if total_alertas_dia > 0 else 0

    ws_res.append([fecha, len(partidos), total_alertas_dia, aciertos_dia, f"{pct}%"])

    try:
        wb.save(ARCHIVO_EXCEL)
        print(f"[cierre] Excel actualizado: {ARCHIVO_EXCEL}")
    except Exception as e:
        print(f"[cierre] Error guardando Excel: {e}")


def cerrar():
    """Ejecuta la Fase 4: cierre del dia."""
    if ya_se_hizo("cierre"):
        print("[cierre] Ya se ejecuto el cierre de hoy.")
        return

    if not ARCHIVO_PARTIDOS.exists():
        print("[cierre] No existe partidos_hoy.json.")
        return

    data = json.loads(ARCHIVO_PARTIDOS.read_text(encoding="utf-8"))
    partidos = data.get("partidos", [])
    fecha = data.get("fecha", datetime.date.today().isoformat())

    if not partidos:
        print("[cierre] No hay partidos para cerrar.")
        return

    print(f"[cierre] Cerrando {len(partidos)} partido(s) del {fecha}...")

    for p in partidos:
        match_id = p["fixture_id"]
        local = p["local"]
        visitante = p["visitante"]

        resultado = obtener_resultado_final(match_id)
        if resultado and resultado.get("terminado"):
            p["resultado_final"] = {
                "goles_home": resultado["goles_home"],
                "goles_away": resultado["goles_away"],
                "score_str": resultado["score_str"],
            }

            # Auditar cada alerta
            goles_h = resultado["goles_home"]
            goles_a = resultado["goles_away"]
            fav_es_local = p.get("favorito_es_local", True)

            for alerta in p.get("alertas_enviadas", []):
                acierto = calcular_acierto(alerta, goles_h, goles_a, fav_es_local)
                alerta["acierto"] = acierto

            print(f"  OK: {local} {goles_h} - {goles_a} {visitante}")
        else:
            print(f"  ?: {local} vs {visitante} — resultado no disponible")

    # Guardar historial
    _guardar_historial_dia(fecha, partidos)

    # Actualizar Excel
    _actualizar_excel(fecha, partidos)

    # Guardar datos actualizados
    ARCHIVO_PARTIDOS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    marcar_hecho("cierre")
    print(f"[cierre] Cierre completado para {fecha}.")


if __name__ == "__main__":
    cerrar()
