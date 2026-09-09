"""
fetch_data.py
-------------
Obtencion de datos de partidos usando EXCLUSIVAMENTE ESPN.

ESPN provee:
  - Fixtures del dia (scoreboard)
  - Stats por equipo: totalShots, shotsOnTarget, blockedShots, possessionPct, etc.
  - Marcador en vivo

xG se estima desde tiros a puerta (SOT, off-target, blocked).
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

    # Extraer hora del partido (UTC)
    fecha_str = evento.get("date", "")
    hora_utc = ""
    if fecha_str:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
            hora_utc = dt.strftime("%H:%M")
        except Exception:
            pass

    return {
        "fixture": {"id": str(evento["id"]), "date": fecha_str},
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
        "_hora_utc": hora_utc,
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

    for slug in ligas:
        try:
            data = _consultar_scoreboard(slug, fecha_iso)
        except Exception as e:
            print(f"[AVISO] ESPN {slug} fallo: {e}")
            continue

        for evento in data.get("events", []):
            fx = _extraer_evento(evento, slug)
            if fx:
                fixtures_por_id[fx["fixture"]["id"]] = fx

    print(f"[ESPN] {len(fixtures_por_id)} fixtures de {len(ligas)} liga(s).")
    return list(fixtures_por_id.values())


# =====================================================================
# ESPN -- xG, stats y datos detallados
# =====================================================================

def extraer_xg_y_stats(match_id):
    """
    Obtiene xG (estimado) y stats en una sola llamada a ESPN summary.

    ESPN boxscore provee:
      totalShots, shotsOnTarget, blockedShots, possessionPct, etc.
    xG se estima desde los tiros.
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

    stats = _extraer_stats_de_boxscore(data)

    xg_home = _estimar_xg(stats, "home")
    xg_away = _estimar_xg(stats, "away")

    return xg_home, xg_away, stats


def _estimar_xg(stats, side):
    """
    Estima xG desde tiros a puerta.
    Promedios historicos:
      - SOT (shot on target): ~0.10 xG por tiro
      - Off-target: ~0.03 xG por tiro
      - Bloqueado: ~0.02 xG por tiro
    """
    sot = stats.get("tiros_puerta", {}).get(side, 0)
    total = stats.get("tiros_totales", {}).get(side, 0)
    blocked = stats.get("tiros_bloqueados", {}).get(side, 0)

    off_target = max(0, total - sot - blocked)

    xg = (sot * 0.10) + (off_target * 0.03) + (blocked * 0.02)
    return round(xg, 2)


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

            if name == "possessionpct" or "possession" in name:
                stats["posesion"][side] = val
            elif name == "totalshots":
                stats["tiros_totales"][side] = int(val)
            elif name == "shotsontarget":
                stats["tiros_puerta"][side] = int(val)
            elif name == "blockedshots":
                stats["tiros_bloqueados"][side] = int(val)
            elif name == "woncorners" or "corners" in name:
                stats["corners"][side] = int(val)
            elif name == "foulscommitted" or "foul" in name:
                stats["faltas"][side] = int(val)

    return stats


_slug_cache = {}

def _detectar_slug_para_match(match_id):
    """Prueba cada slug de liga para encontrar el match. Usa cache."""
    if match_id in _slug_cache:
        return _slug_cache[match_id]

    for slug in LIGAS_ESPN:
        try:
            url = f"{BASE_ESPN_SITE}/{slug}/summary?event={match_id}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get("header", {}).get("competitions"):
                    _slug_cache[match_id] = slug
                    return slug
        except Exception:
            continue
    return None


def _stats_vacias():
    return {
        "posesion": {"home": 50.0, "away": 50.0},
        "tiros_totales": {"home": 0, "away": 0},
        "tiros_puerta": {"home": 0, "away": 0},
        "tiros_bloqueados": {"home": 0, "away": 0},
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
    """Obtiene marcador en vivo, estado y minuto desde ESPN."""
    if not liga_slug or liga_slug == "all":
        return None, None, None, None

    url = f"{BASE_ESPN_SITE}/{liga_slug}/summary?event={fixture_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()

        header = data.get("header", {})
        competitions = header.get("competitions", [{}])
        if not competitions:
            return None, None, None, None
        comp = competitions[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None, None, None, None

        goles_home = int(competitors[0].get("score", 0))
        goles_away = int(competitors[1].get("score", 0))

        comp_status = comp.get("status", {})
        estado = comp_status.get("type", {}).get("state")
        clock = comp_status.get("displayClock", None)

        return goles_home, goles_away, estado, clock
    except Exception:
        return None, None, None, None
