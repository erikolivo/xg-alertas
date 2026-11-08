"""
seleccionar_partidos.py
-----------------------
FASE 1: Obtiene TODOS los partidos del dia desde ESPN y prepara
partidos_hoy.json para vigilancia automatica.

Si ya existen partidos seleccionados del dia, los COMPLEMENTA
(nuevos fixtures) sin borrar los existentes ni su historial.
"""

import datetime
import json
from pathlib import Path

from fetch_data import obtener_fixtures_por_fecha

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"
VERSION_SELECCION = 3


def _partido_para_vigilar(fx):
    """Construye el registro del partido para partidos_hoy.json."""
    fav = fx.get("_favorito_es_local")
    if fav is True:
        nombre_fav = fx["teams"]["home"]["name"]
    elif fav is False:
        nombre_fav = fx["teams"]["away"]["name"]
    else:
        nombre_fav = ""

    return {
        "fixture_id": fx["fixture"]["id"],
        "liga_slug": fx.get("_liga_slug", ""),
        "liga": fx["league"]["name"],
        "liga_pais": fx["league"]["country"],
        "local": fx["teams"]["home"]["name"],
        "visitante": fx["teams"]["away"]["name"],
        "home_id": fx["teams"]["home"]["id"],
        "away_id": fx["teams"]["away"]["id"],
        "hora_utc": fx.get("_hora_utc", ""),
        "favorito": nombre_fav,
        "favorito_es_local": fav,
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


def _cargar_existentes():
    """Carga partidos existentes del dia si el archivo es de hoy."""
    if not ARCHIVO_SALIDA.exists():
        return None, []
    try:
        data = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8"))
        fecha_archivo = data.get("fecha", "")
        hoy = datetime.date.today().isoformat()
        if fecha_archivo == hoy:
            return data, data.get("partidos", [])
    except Exception:
        pass
    return None, []


# Solo se refrescan estos campos. El historial del partido
# (alertas_enviadas, snapshots, resultado_final) NUNCA se toca.
CAMPOS_METADATA = (
    "liga_slug", "liga", "liga_pais", "local", "visitante",
    "home_id", "away_id", "hora_utc", "favorito", "favorito_es_local",
)


def _actualizar_metadata(partido, fx):
    """Refresca horario/cuotas/nombres conservando alertas y snapshots."""
    nuevo = _partido_para_vigilar(fx)
    for campo in CAMPOS_METADATA:
        valor = nuevo.get(campo)
        if valor in (None, ""):
            # No borrar un dato bueno (p. ej. el favorito) porque ESPN deje
            # de publicar cuotas en una lectura posterior.
            continue
        partido[campo] = valor
    partido["actualizado_en"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    partido["version_seleccion"] = VERSION_SELECCION


def seleccionar(forzar=False):
    hoy = datetime.date.today()
    fecha_iso = hoy.isoformat()

    # Cargar existentes del dia
    data_existente, partidos_existentes = _cargar_existentes()
    por_id = {p["fixture_id"]: p for p in partidos_existentes}

    if por_id and not forzar:
        print(f"[seleccion] Ya hay {len(por_id)} partidos seleccionados hoy. Use --forzar para complementar.")
        return partidos_existentes

    print(f"[seleccion] Buscando partidos del dia {fecha_iso} en ESPN...")
    fixtures = obtener_fixtures_por_fecha(fecha_iso)
    if not fixtures:
        print("[seleccion] No se encontraron fixtures en ESPN para hoy.")
        return partidos_existentes

    nuevos = 0
    actualizados = 0
    for fx in fixtures:
        fid = fx["fixture"]["id"]
        existente = por_id.get(fid)
        if existente is None:
            partido = _partido_para_vigilar(fx)
            partidos_existentes.append(partido)
            por_id[fid] = partido
            nuevos += 1
        elif forzar:
            # Refrescar metadatos SIN borrar alertas, snapshots ni resultado.
            _actualizar_metadata(existente, fx)
            actualizados += 1

    # Los partidos que ya no aparecen en ESPN se conservan: pueden tener
    # alertas y snapshots que la Fase 4 necesita auditar.

    # Guardar
    DATA_DIR.mkdir(exist_ok=True)
    salida = {
        "fecha": fecha_iso,
        "generado": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total": len(partidos_existentes),
        "partidos": partidos_existentes,
    }
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    ligas = set(p["liga"] for p in partidos_existentes)
    print(f"\n[seleccion] {len(partidos_existentes)} partidos total "
          f"({nuevos} nuevos, {actualizados} actualizados) de {len(ligas)} liga(s).")
    for liga in sorted(ligas):
        count = sum(1 for p in partidos_existentes if p["liga"] == liga)
        print(f"  - {liga}: {count}")

    return partidos_existentes


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--forzar", action="store_true")
    args = parser.parse_args()
    seleccionar(forzar=args.forzar)
