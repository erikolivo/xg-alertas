"""
seleccionar_partidos.py
-----------------------
FASE 1: Lee los favoritos de Google Sheets y los empareja con fixtures
de FotMob del dia, preparando partidos_hoy.json para vigilancia.
"""

import datetime
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

from fetch_data import obtener_fixtures_por_fecha
from google_favoritos import normalizar, obtener_favoritos_google

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"
ARCHIVO_CACHE_ALIAS = DATA_DIR / "alias_equipos_cache.json"
ARCHIVO_PENDIENTES = DATA_DIR / "pendientes_revision.json"
ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))
VERSION_SELECCION = 1

# Alias fijos a mano
ALIAS_EQUIPOS = {
    "wolves": "wolverhampton wanderers",
    "aarhus": "agf",
}
SUFIJOS_EQUIPO = {"fc", "cf", "fk", "ff", "sc", "afc", "ac"}

_cache_alias_memoria = None


def _cargar_cache_alias():
    global _cache_alias_memoria
    if _cache_alias_memoria is None:
        if ARCHIVO_CACHE_ALIAS.exists():
            try:
                _cache_alias_memoria = json.loads(ARCHIVO_CACHE_ALIAS.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _cache_alias_memoria = {}
        else:
            _cache_alias_memoria = {}
    return _cache_alias_memoria


def _registrar_alias_aprendido(nombre_hoja, nombre_fotmob):
    """Guarda nombre_de_la_hoja -> nombre_oficial_FotMob."""
    clave = normalizar(nombre_hoja)
    if not clave or clave == normalizar(nombre_fotmob):
        return
    cache = _cargar_cache_alias()
    if cache.get(clave) == nombre_fotmob:
        return
    cache[clave] = nombre_fotmob
    ARCHIVO_CACHE_ALIAS.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _registrar_pendiente(entrada, motivo, fecha):
    """Log de partidos que no se ubicaron en FotMob."""
    pendientes = []
    if ARCHIVO_PENDIENTES.exists():
        try:
            pendientes = json.loads(ARCHIVO_PENDIENTES.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pendientes = []
    pendientes.append({
        "fecha": fecha, "local": entrada["local"], "visitante": entrada["visitante"],
        "favorito": entrada.get("favorito"), "motivo": motivo,
    })
    pendientes = pendientes[-200:]
    ARCHIVO_PENDIENTES.write_text(json.dumps(pendientes, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalizar_equipo(nombre):
    """Normaliza un nombre para comparacion fuzzy."""
    nombre = normalizar(nombre)
    for sufijo in SUFIJOS_EQUIPO:
        nombre = re.sub(rf"\b{sufijo}\b", "", nombre)
    return re.sub(r"\s+", " ", nombre).strip()


import re


def _coincide(nombre_hoja, nombre_fotmob, umbral=0.75):
    """Verifica si dos nombres coinciden (fuzzy)."""
    nh = _normalizar_equipo(nombre_hoja)
    nf = _normalizar_equipo(nombre_fotmob)

    if nh == nf:
        return True
    if nh in nf or nf in nh:
        return True

    # Alias fijos
    if nh in ALIAS_EQUIPOS and ALIAS_EQUIPOS[nh] in nf:
        return True

    # Cache
    cache = _cargar_cache_alias()
    if cache.get(nh) == nf:
        return True

    ratio = SequenceMatcher(None, nh, nf).ratio()
    return ratio >= umbral


def _buscar_fixture(favorito, fixtures):
    """Busca el fixture que coincide con un favorito de la hoja."""
    local_hoja = favorito.get("local", "")
    visitante_hoja = favorito.get("visitante", "")

    for fx in fixtures:
        local_fotmob = fx["teams"]["home"]["name"]
        visitante_fotmob = fx["teams"]["away"]["name"]

        if _coincide(local_hoja, local_fotmob) and _coincide(visitante_hoja, visitante_fotmob):
            # Aprender alias si fue fuzzy match
            if not _coincide(local_hoja, local_fotmob, umbral=0.95):
                _registrar_alias_aprendido(local_hoja, local_fotmob)
            if not _coincide(visitante_hoja, visitante_fotmob, umbral=0.95):
                _registrar_alias_aprendido(visitante_hoja, visitante_fotmob)
            return fx

    return None


def _lado_favorito(favorito):
    """Determina que lado es el favorito (home/away)."""
    fav = favorito.get("favorito", "")
    if not fav:
        return None
    fav_lower = normalizar(fav)
    local = normalizar(favorito.get("local", ""))
    visitante = normalizar(favorito.get("visitante", ""))

    if fav_lower in local or local in fav_lower:
        return "home"
    if fav_lower in visitante or visitante in fav_lower:
        return "away"
    return None


def _partido_para_vigilar(fx, favorito):
    """Construye el registro del partido para partidos_hoy.json."""
    lado_fav = _lado_favorito(favorito)

    return {
        "fixture_id": fx["fixture"]["id"],
        "fotmob_league_id": fx.get("_fotmob_league_id", ""),
        "liga": fx["league"]["name"],
        "liga_pais": fx["league"]["country"],
        "local": fx["teams"]["home"]["name"],
        "visitante": fx["teams"]["away"]["name"],
        "home_id": fx["teams"]["home"]["id"],
        "away_id": fx["teams"]["away"]["id"],
        "hora": fx.get("_hora_local", ""),
        "favorito": favorito.get("favorito", ""),
        "favorito_es_local": lado_fav == "home" if lado_fav else None,
        "tipo_pronostico": favorito.get("tipo_pronostico", "favorito_directo"),
        "confianza": favorito.get("confianza", ""),
        "cuota_local": favorito.get("cuota_local"),
        "cuota_visitante": favorito.get("cuota_visitante"),
        "seleccionado_en": datetime.datetime.utcnow().isoformat() + "Z",
        "version_seleccion": VERSION_SELECCION,
        "alertas_enviadas": [],
        "snapshots": [],
        "resultado_final": None,
    }


def ya_se_completo_hoy():
    """Verifica si la fase de seleccion ya corrio hoy."""
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
    """Ejecuta la Fase 1: seleccion de partidos del dia."""
    if ya_se_completo_hoy() and not forzar:
        print("[seleccion] Ya se completó la selección de hoy.")
        return []

    hoy = datetime.date.today()
    fecha_iso = hoy.isoformat()

    print(f"[seleccion] Buscando favoritos para {fecha_iso}...")
    favoritos = obtener_favoritos_google()
    if not favoritos:
        print("[seleccion] No se encontraron favoritos en Google Sheets.")
        return []

    print(f"[seleccion] {len(favoritos)} favorito(s) encontrado(s).")

    print("[seleccion] Buscando fixtures en FotMob...")
    fixtures = obtener_fixtures_por_fecha(fecha_iso)
    if not fixtures:
        print("[seleccion] No se encontraron fixtures en FotMob para hoy.")
        return []

    partidos = []
    no_encontrados = []

    for fav in favoritos:
        fx = _buscar_fixture(fav, fixtures)
        if fx:
            partido = _partido_para_vigilar(fx, fav)
            partidos.append(partido)
            print(f"  OK: {partido['local']} vs {partido['visitante']} ({partido['liga']})")
        else:
            no_encontrados.append(fav)
            _registrar_pendiente(fav, "no_encontrado_en_fotmob", fecha_iso)
            print(f"  FALLO: {fav.get('local', '?')} vs {fav.get('visitante', '?')}")

    # Guardar resultado
    DATA_DIR.mkdir(exist_ok=True)
    salida = {
        "fecha": fecha_iso,
        "generado": datetime.datetime.utcnow().isoformat() + "Z",
        "total": len(partidos),
        "partidos": partidos,
    }
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[seleccion] {len(partidos)} partido(s) seleccionado(s) para vigilancia.")
    if no_encontrados:
        print(f"[seleccion] {len(no_encontrados)} favorito(s) no ubicado(s) en FotMob.")

    return partidos


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--forzar", action="store_true", help="Forzar re-seleccion")
    args = parser.parse_args()
    seleccionar(forzar=args.forzar)
