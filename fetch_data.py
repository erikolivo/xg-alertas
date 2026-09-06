"""
fetch_data.py
-------------
Obtencion de datos de partidos desde FotMob.

Endpoints usados:
  - /api/matches?date=YYYYMMDD    -> fixtures del dia
  - /api/matchDetails?matchId=X   -> datos completos de un partido (xG, stats, eventos)
  - /api/data/match-score?matchId=X -> marcador ligero (polling rapido)
  - /api/teams?id=X               -> forma del equipo (ultimos resultados)

Formato interno de fixture:
  {
    "fixture": {"id": "<fotmob_match_id>", "date": "<UTC ISO>"},
    "teams": {
      "home": {"id": "<fotmob_team_id>", "name": "<nombre>"},
      "away": {"id": "<fotmob_team_id>", "name": "<nombre>"},
    },
    "league": {"name": "<nombre_liga>", "country": "<pais>"},
    "_fotmob_league_id": "<league_id>",
    "_estado": "pre"|"in"|"post",
    "_goles_local": int|None,
    "_goles_visitante": int|None,
    "_hora_local": "HH:MM",
  }
"""

import re
from datetime import datetime

from fotmob_client import api_get, next_data_get

# =====================================================================
# Ligas de FotMob que se monitorean.
# Key: fotmob league ID, Value: (nombre, pais).
# Para agregar una liga, buscar su ID en /api/allLeagues.
# =====================================================================

LIGAS_FOTMOB = {
    "47": ("Premier League", "England"),
    "87": ("La Liga", "Spain"),
    "55": ("Bundesliga", "Germany"),
    "53": ("Serie A", "Italy"),
    "54": ("Ligue 1", "France"),
    "88": ("Eredivisie", "Netherlands"),
    "61": ("Liga Portugal", "Portugal"),
    "94": ("Primeira Liga", "Brazil"),
    "71": ("Superliga Argentina", "Argentina"),
    "119": ("Liga Pro Ecuador", "Ecuador"),
    "79": ("Liga BetPlay", "Colombia"),
    "210": ("Superliga Turca", "Turkey"),
    "43": ("Jupiler Pro League", "Belgium"),
    "46": ("Scottish Premiership", "Scotland"),
    "168": ("Allsvenskan", "Sweden"),
    "132": ("Eliteserien", "Norway"),
    "239": ("Liga MX", "Mexico"),
    "242": ("MLS", "USA"),
}


def _extraer_estado(match):
    """Extrae el estado del partido desde el formato de FotMob."""
    status = match.get("status", {})
    if status.get("finished"):
        return "post"
    if status.get("started"):
        return "in"
    return "pre"


def _extraer_hora_local(match):
    """Extrae la hora local del partido."""
    status = match.get("status", {})
    utc_time = status.get("utcTime", "")
    if not utc_time:
        return ""
    try:
        dt = datetime.fromisoformat(utc_time.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _normalizar_fixture(match, liga_info):
    """Convierte un match crudo de FotMob al formato interno."""
    home = match.get("home", {})
    away = match.get("away", {})
    status = match.get("status", {})
    score_str = status.get("scoreStr", "")

    goles_home = None
    goles_away = None
    if score_str and " - " in score_str:
        partes = score_str.split(" - ")
        try:
            goles_home = int(partes[0].strip())
            goles_away = int(partes[1].strip())
        except (ValueError, IndexError):
            pass

    return {
        "fixture": {
            "id": str(match.get("id", "")),
            "date": status.get("utcTime", ""),
        },
        "teams": {
            "home": {
                "id": str(home.get("id", "")),
                "name": home.get("name", ""),
            },
            "away": {
                "id": str(away.get("id", "")),
                "name": away.get("name", ""),
            },
        },
        "league": {
            "name": liga_info[0],
            "country": liga_info[1],
        },
        "_fotmob_league_id": str(match.get("leagueId", "")),
        "_estado": _extraer_estado(match),
        "_goles_local": goles_home,
        "_goles_visitante": goles_away,
        "_hora_local": _extraer_hora_local(match),
    }


def obtener_fixtures_por_fecha(fecha_iso, ligas_ids=None):
    """
    Obtiene todos los partidos de una fecha desde FotMob.

    'fecha_iso': "YYYY-MM-DD"
    'ligas_ids': lista de IDs de liga a filtrar (None = todas las de LIGAS_FOTMOB)

    Devuelve lista de fixtures en formato interno.
    """
    fecha_fmt = fecha_iso.replace("-", "")
    data = api_get("matches", params={"date": fecha_fmt})

    if not data:
        print(f"[fetch] No se pudieron obtener fixtures de FotMob para {fecha_iso}")
        return []

    fixtures = []
    for liga in data.get("leagues", []):
        liga_id = str(liga.get("primaryId", ""))
        if liga_id not in LIGAS_FOTMOB:
            continue
        if ligas_ids and liga_id not in ligas_ids:
            continue

        liga_info = LIGAS_FOTMOB[liga_id]
        for match in liga.get("matches", []):
            fx = _normalizar_fixture(match, liga_info)
            fixtures.append(fx)

    print(f"[fetch] FotMob ({fecha_iso}): {len(fixtures)} fixtures encontrados.")
    return fixtures


def obtener_detalles_partido(match_id):
    """
    Obtiene los detalles completos de un partido: xG, stats, eventos, etc.
    Devuelve el dict raw de FotMob o None si falla.
    """
    data = api_get("matchDetails", params={"matchId": match_id})
    if not data:
        print(f"[fetch] No se pudieron obtener detalles del partido {match_id}")
        return None
    return data


def _extraer_stats_grupo(stats_raw, titulo):
    """Extrae un grupo de stats por titulo (ej. 'Top stats', 'Shots')."""
    for grupo in stats_raw:
        if grupo.get("title", "").lower() == titulo.lower():
            return grupo.get("stats", [])
    return []


def _parsear_stat_value(valor):
    """Parsea un valor de stat que puede ser int, float, string o None."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        valor = valor.replace("%", "").replace("'", "").strip()
        try:
            return float(valor)
        except ValueError:
            return 0.0
    return 0.0


def extraer_xg(detalles):
    """
    Extrae xG home y away desde los detalles del partido.
    Devuelve (xg_home, xg_away) o (None, None).
    """
    if not detalles:
        return None, None

    try:
        content = detalles.get("content", {})
        stats = content.get("stats", {})
        periods = stats.get("Periods", {})
        all_stats = periods.get("All", {}).get("stats", [])

        for grupo in all_stats:
            if "expected" in grupo.get("title", "").lower():
                for stat in grupo.get("stats", []):
                    if "xg" in stat.get("title", "").lower() or "expected goals" in stat.get("title", "").lower():
                        vals = stat.get("stats", [])
                        if len(vals) >= 2:
                            return _parsear_stat_value(vals[0]), _parsear_stat_value(vals[1])
    except Exception as e:
        print(f"[fetch] Error extrayendo xG: {e}")

    return None, None


def extraer_stats_partido(detalles):
    """
    Extrae estadisticas clave del partido: posesion, tiros, corners, etc.
    Devuelve dict con home/away para cada stat.
    """
    resultado = {
        "posesion": {"home": 50.0, "away": 50.0},
        "tiros_totales": {"home": 0, "away": 0},
        "tiros_puerta": {"home": 0, "away": 0},
        "corners": {"home": 0, "away": 0},
        "faltas": {"home": 0, "away": 0},
    }

    if not detalles:
        return resultado

    try:
        content = detalles.get("content", {})
        stats = content.get("stats", {})
        periods = stats.get("Periods", {})
        all_stats = periods.get("All", {}).get("stats", [])

        mapa_stats = {
            "ball possession": "posesion",
            "total shots": "tiros_totales",
            "shots on target": "tiros_puerta",
            "corner kicks": "corners",
            "fouls": "faltas",
        }

        for grupo in all_stats:
            for stat in grupo.get("stats", []):
                titulo = stat.get("title", "").lower()
                clave = mapa_stats.get(titulo)
                if clave:
                    vals = stat.get("stats", [])
                    if len(vals) >= 2:
                        h = _parsear_stat_value(vals[0])
                        a = _parsear_stat_value(vals[1])
                        if clave == "posesion":
                            resultado[clave] = {"home": h, "away": a}
                        else:
                            resultado[clave] = {"home": int(h), "away": int(a)}
    except Exception as e:
        print(f"[fetch] Error extrayendo stats: {e}")

    return resultado


def extraer_eventos(detalles):
    """
    Extrae eventos del partido (goles, tarjetas, cambios).
    Devuelve lista de eventos chronologicos.
    """
    eventos = []
    if not detalles:
        return eventos

    try:
        content = detalles.get("content", {})
        match_facts = content.get("matchFacts", {})
        incidents = match_facts.get("events", {}).get("incidents", [])

        for inc in incidents:
            evento = {
                "tipo": inc.get("type", ""),
                "minuto": inc.get("minuteLabel", ""),
                "equipo": "home" if inc.get("isHome") else "away",
                "jugador": inc.get("playerName", ""),
                "detalle": inc.get("card") or inc.get("goalType") or "",
            }
            eventos.append(evento)
    except Exception as e:
        print(f"[fetch] Error extrayendo eventos: {e}")

    return eventos


def extraer_forma_equipo(team_id):
    """
    Obtiene la forma reciente de un equipo (ultimos W/D/L).
    Devuelve lista de dicts con resultado, marcador y rival.
    """
    data = api_get("teams", params={"id": team_id})
    if not data:
        return []

    forma = []
    try:
        overview = data.get("overview", {})
        for item in overview.get("form", []):
            forma.append({
                "resultado": item.get("result", ""),
                "score": item.get("score", ""),
                "rival": item.get("opponent", ""),
            })
    except Exception as e:
        print(f"[fetch] Error extrayendo forma del equipo {team_id}: {e}")

    return forma


def obtener_resultado_final(match_id):
    """
    Obtiene el resultado final de un partido terminado.
    Devuelve dict con goles_home, goles_away, estado o None.
    """
    data = api_get("matchDetails", params={"matchId": match_id})
    if not data:
        return None

    try:
        header = data.get("header", {})
        teams = header.get("teams", [])
        status = header.get("status", {})

        if len(teams) < 2:
            return None

        goles_home = teams[0].get("score")
        goles_away = teams[1].get("score")

        return {
            "goles_home": int(goles_home) if goles_home is not None else None,
            "goles_away": int(goles_away) if goles_away is not None else None,
            "terminado": status.get("finished", False),
            "score_str": status.get("scoreStr", ""),
        }
    except Exception as e:
        print(f"[fetch] Error obteniendo resultado final {match_id}: {e}")
        return None
