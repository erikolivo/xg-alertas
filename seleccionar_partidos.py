"""
seleccionar_partidos.py
-----------------------
FASE 1: Obtiene TODOS los partidos del dia desde ESPN y prepara
partidos_hoy.json para vigilancia automatica.

Ya no depende de Google Sheets / Excel.
ESPN cubre ~16 ligas principales con xG disponible.
"""

import datetime
import json
from pathlib import Path

from fetch_data import obtener_fixtures_por_fecha

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"
VERSION_SELECCION = 2


def _partido_para_vigilar(fx):
    """Construye el registro del partido para partidos_hoy.json."""
    return {
        "fixture_id": fx["fixture"]["id"],
        "liga_slug": fx.get("_liga_slug", ""),
        "liga": fx["league"]["name"],
        "liga_pais": fx["league"]["country"],
        "local": fx["teams"]["home"]["name"],
        "visitante": fx["teams"]["away"]["name"],
        "home_id": fx["teams"]["home"]["id"],
        "away_id": fx["teams"]["away"]["id"],
        "hora": fx.get("_hora_local", ""),
        "favorito": "",
        "favorito_es_local": None,
        "tipo_pronostico": "monitoreo_automatico",
        "confianza": "",
        "cuota_local": None,
        "cuota_visitante": None,
        "seleccionado_en": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version_seleccion": VERSION_SELECCION,
        "alertas_enviadas": [],
        "snapshots": [],
        "resultado_final": None,
    }


def ya_se_completo_hoy():
    if not ARCHIVO_SALIDA.exists():
        return False
    try:
        data = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8"))
        fecha_archivo = data.get("fecha", "")
        hoy = datetime.date.today().isoformat()
        return fecha_archivo == hoy
    except Exception:
        return False


def seleccionar(forzar=False):
    if ya_se_completo_hoy() and not forzar:
        print("[seleccion] Ya se completo la seleccion de hoy.")
        return []

    hoy = datetime.date.today()
    fecha_iso = hoy.isoformat()

    print(f"[seleccion] Buscando partidos del dia {fecha_iso} en ESPN...")
    fixtures = obtener_fixtures_por_fecha(fecha_iso)
    if not fixtures:
        print("[seleccion] No se encontraron fixtures en ESPN para hoy.")
        return []

    partidos = []
    for fx in fixtures:
        partido = _partido_para_vigilar(fx)
        partidos.append(partido)

    # Guardar
    DATA_DIR.mkdir(exist_ok=True)
    salida = {
        "fecha": fecha_iso,
        "generado": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total": len(partidos),
        "partidos": partidos,
    }
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    ligas = set(p["liga"] for p in partidos)
    print(f"\n[seleccion] {len(partidos)} partidos de {len(ligas)} liga(s) listos para vigilancia.")
    for liga in sorted(ligas):
        count = sum(1 for p in partidos if p["liga"] == liga)
        print(f"  - {liga}: {count}")

    return partidos


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--forzar", action="store_true")
    args = parser.parse_args()
    seleccionar(forzar=args.forzar)
