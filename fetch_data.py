"""
fetch_data.py
-------------
Obtencion de datos de partidos usando EXCLUSIVAMENTE ESPN.

ESPN provee:
  - Fixtures del dia (scoreboard)
  - xG (Expected Goals) por equipo (summary -> leaders -> expectedGoals)
  - xGC (Expected Goals Conceded) por equipo
  - Estadisticas del partido (keyStats)
  - Marcador en vivo

No se usa FotMob (endpoints rotos / sin API publica).
"""

import re
from datetime import datetime

import requests

TIMEOUT = 20

BASE_ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

LIGAS_ESPN = {
    "eng.1": ("Premier League", "England"),
    "esp.1": ("La Liga", "Spain"),
    "ger.1": ("Bundesliga", "Germany"),
    "ita.1": ("Serie A", "Italy"),
    "fra.1": ("Ligue 1", "France"),
    "ned.1": ("Eredivisie", "Netherlands"),
    "por.1": ("Liga Portugal", "Portugal"),
    "bra.1": ("Brasileirao", "Brazil"),
    "arg.1": ("Liga Argentina", "Argentina"),
    "ecu.1": ("Liga Pro Ecuador", "Ecuador"),
    "col.1": ("Liga BetPlay", "Colombia"),
    "tur.1": ("Superliga Turca", "Turkey"),
    "bel.1": ("Jupiler Pro League", "Belgium"),
    "sco.1": ("Scottish Premiership", "Scotland"),
    "usa.1": ("MLS", "USA"),
    "mex.1": ("Liga MX", "Mexico"),
}

# =====================================================================
# ESPN -- fixtures y resultados
# =====================================================================

def _fecha_espn(fecha_iso):
    return fecha_iso.replace("-", "")


def _extraer_evento(evento, liga_slug):
    try:
        comp = evento["competitions"][0]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
    except (KeyError, IndexError, StopIteration):
        return None

    liga = evento.get("league", {})
    status = comp.get("status", {}) or evento.get("status", {})
    estado = status.get("type", {}).get("state")

    # ESPN no retorna league.name en scoreboard, usar LIGAS_ESPN
    liga_info = LIGAS_ESPN.get(liga_slug, ("Desconocida", ""))

    def _goles(competitor):
        try:
            return int(competitor.get("score", 0))
        except (TypeError, ValueError):
            return None

    def _safe_team_id(team_obj):
        tid = team_obj.get("id")
        if isinstance(tid, list):
            tid = tid[0] if tid else None
        return str(tid) if tid else "0"

    return {
        "fixture": {"id": str(evento["id"]), "date": evento.get("date")},
        "teams": {
            "home": {"id": _safe_team_id(home["team"]), "name": home["team"].get("displayName")},
            "away": {"id": _safe_team_id(away["team"]), "name": away["team"].get("displayName")},
        },
        "league": {
            "country": liga.get("country") or liga_info[1],
            "name": liga.get("name") or liga_info[0],
        },
        "_liga_slug": liga_slug,
        "_estado": estado,
        "_goles_local": _goles(home),
        "_goles_visitante": _goles(away),
        "_hora_local": "",
    }


def _consultar_scoreboard(slug, fecha_iso):
    url = f"{BASE_ESPN_SITE}/{slug}/scoreboard?dates={_fecha_espn(fecha_iso)}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def obtener_fixtures_por_fecha(fecha_iso, ligas=None):
    if ligas is None:
        ligas = list(LIGAS_ESPN.keys())

    fixtures_por_id = {}

    try:
        data = _consultar_scoreboard("all", fecha_iso)
        for evento in data.get("events", []):
            fx = _extraer_evento(evento, "all")
            if fx:
                fixtures_por_id[fx["fixture"]["id"]] = fx
        print(f"[ESPN] global ({fecha_iso}): {len(fixtures_por_id)} fixtures.")
    except Exception as e:
        print(f"[AVISO] ESPN global fallo para {fecha_iso}: {e}")

    nuevos = 0
    fallidas = []
    for slug in ligas:
        try:
            data = _consultar_scoreboard(slug, fecha_iso)
        except Exception as e:
            fallidas.append(slug)
            continue

        for evento in data.get("events", []):
            eid = str(evento["id"])
            fx = _extraer_evento(evento, slug)
            if fx:
                if eid in fixtures_por_id:
                    # Actualizar slug y liga si el anterior era "all"
                    if fixtures_por_id[eid].get("_liga_slug") == "all":
                        fixtures_por_id[eid]["_liga_slug"] = slug
                    # Actualizar nombre de liga si estaba vacio o era "Desconocida"
                    existing = fixtures_por_id[eid]
                    old_name = existing["league"]["name"]
                    new_name = fx["league"]["name"]
                    if new_name and (not old_name or old_name == "Desconocida"):
                        existing["league"]["name"] = new_name
                    new_country = fx["league"]["country"]
                    if new_country and not existing["league"]["country"]:
                        existing["league"]["country"] = new_country
                else:
                    fixtures_por_id[eid] = fx
                    nuevos += 1

    print(f"[ESPN] {len(ligas)} liga(s), {nuevos} adicionales.")
    if fallidas:
        print(f"[AVISO] {len(fallidas)} liga(s) fallaron: {fallidas[:5]}")

    return list(fixtures_por_id.values())


# =====================================================================
# ESPN -- xG, stats y datos detallados
# =====================================================================

def extraer_xg_y_stats(match_id):
    """
    Obtiene xG y stats en una sola llamada a ESPN summary.

    ESPN structure:
      leaders[0] = home team leaders
        leaders[0].leaders[0] = best player
          .statistics[2] = expectedGoals (xG)
      leaders[1] = away team leaders
        leaders[1].leaders[0].statistics[2] = expectedGoals (xG)
    """
    slug = _detectar_slug_para_match(match_id)
    if not slug:
        return None, None, _stats_vacias()

    url = f"{BASE_ESPN_SITE}/{slug}/summary?event={match_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[ESPN] Error summary {match_id}: {e}")
        return None, None, _stats_vacias()

    xg_home, xg_away = _extraer_xg_de_leaders(data)
    stats = _extraer_stats_de_boxscore(data)

    return xg_home, xg_away, stats


def _extraer_xg_de_leaders(data):
    """Extrae xG home/away desde leaders de ESPN."""
    xg_home = None
    xg_away = None

    leaders = data.get("leaders", [])
    for i, team_leaders in enumerate(leaders[:2]):
        for cat in team_leaders.get("leaders", []):
            for leader in cat.get("leaders", []):
                for stat in leader.get("statistics", []):
                    if stat.get("abbreviation") == "xG":
                        try:
                            val = float(stat.get("displayValue", 0))
                            if i == 0:
                                xg_home = val
                            else:
                                xg_away = val
                        except (ValueError, TypeError):
                            pass

    return xg_home, xg_away


def _extraer_stats_de_boxscore(data):
    """Extrae estadisticas del partido desde ESPN boxscore."""
    stats = _stats_vacias()

    boxscore = data.get("boxscore", {})
    teams = boxscore.get("teams", [])

    if len(teams) < 2:
        return stats

    for i, team_data in enumerate(teams[:2]):
        side = "home" if i == 0 else "away"
        for stat in team_data.get("statistics", []):
            name = stat.get("name", "").lower()
            display = stat.get("displayValue", "0")

            try:
                val = float(display.replace("%", "").strip())
            except (ValueError, TypeError):
                val = 0.0

            if name == "possession" or "possession" in name:
                stats["posesion"][side] = val
            elif name == "total shots" or "shots total" in name:
                stats["tiros_totales"][side] = int(val)
            elif name == "shots on goal" or "shots on target" in name or "sog" in name:
                stats["tiros_puerta"][side] = int(val)
            elif name == "corner kicks" or "corners" in name:
                stats["corners"][side] = int(val)
            elif name == "fouls" or "foul" in name:
                stats["faltas"][side] = int(val)

    return stats


def _detectar_slug_para_match(match_id):
    """Prueba cada slug de liga para encontrar el match."""
    for slug in LIGAS_ESPN:
        try:
            url = f"{BASE_ESPN_SITE}/{slug}/summary?event={match_id}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get("header", {}).get("competitions"):
                    return slug
        except Exception:
            continue
    return None


def _stats_vacias():
    return {
        "posesion": {"home": 50.0, "away": 50.0},
        "tiros_totales": {"home": 0, "away": 0},
        "tiros_puerta": {"home": 0, "away": 0},
        "corners": {"home": 0, "away": 0},
        "faltas": {"home": 0, "away": 0},
    }


def extraer_forma_equipo(team_id):
    """Forma reciente del equipo (ultimos W/D/L)."""
    try:
        url = f"{BASE_ESPN_SITE}/teams/{team_id}/schedule"
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()

        forma = []
        for evento in data.get("events", [])[-5:]:
            try:
                comp = evento["competitions"][0]
                status = comp.get("status", {})
                if status.get("type", {}).get("state") != "post":
                    continue
                home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
                away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
                gh = int(home.get("score", 0))
                ga = int(away.get("score", 0))
                if gh > ga:
                    forma.append("W")
                elif gh < ga:
                    forma.append("L")
                else:
                    forma.append("D")
            except Exception:
                continue

        return forma[-5:]
    except Exception:
        return []


def obtener_resultado_final(fixture_id, liga_slug):
    """Obtiene el resultado final de un partido terminado."""
    if not liga_slug or liga_slug == "all":
        return None

    url = f"{BASE_ESPN_SITE}/{liga_slug}/summary?event={fixture_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()

        header = data.get("header", {})
        competitions = header.get("competitions", [{}])
        if not competitions:
            return None
        comp = competitions[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None

        comp_status = comp.get("status", {})
        terminado = comp_status.get("type", {}).get("state") == "post"

        return {
            "goles_home": int(competitors[0].get("score", 0)),
            "goles_away": int(competitors[1].get("score", 0)),
            "terminado": terminado,
        }
    except Exception as e:
        print(f"[ESPN] Error resultado {fixture_id}: {e}")
        return None


def obtener_score_en_vivo(fixture_id, liga_slug):
    """Obtiene marcador en vivo y estado desde ESPN."""
    if not liga_slug or liga_slug == "all":
        return None, None, None

    url = f"{BASE_ESPN_SITE}/{liga_slug}/summary?event={fixture_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()

        header = data.get("header", {})
        competitions = header.get("competitions", [{}])
        if not competitions:
            return None, None, None
        comp = competitions[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None, None, None

        goles_home = int(competitors[0].get("score", 0))
        goles_away = int(competitors[1].get("score", 0))

        # El estado esta en competitions[0].status, NO en header.status
        comp_status = comp.get("status", {})
        estado = comp_status.get("type", {}).get("state")

        return goles_home, goles_away, estado
    except Exception:
        return None, None, None
