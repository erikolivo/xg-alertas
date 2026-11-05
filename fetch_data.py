"""
fetch_data.py
-------------
Obtencion de datos de partidos usando EXCLUSIVAMENTE ESPN.

ESPN provee:
  - Fixtures del dia (scoreboard)
  - Stats por equipo: totalShots, shotsOnTarget, blockedShots, possessionPct, etc.
  - Marcador en vivo
  - Coordenadas de cada tiro en el play-by-play (commentary)

xG se calcula con un modelo logistico de distancia y angulo al arco usando
las coordenadas de cada tiro. Si el partido no tiene coordenadas se cae a
una estimacion por volumen de tiros.
"""

import math
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


def _favorito_de_odds(odds):
    """
    Devuelve True si el local es favorito, False si lo es el visitante,
    None si no hay cuotas disponibles.
    """
    for entry in odds or []:
        if not isinstance(entry, dict):
            continue
        home = entry.get("homeTeamOdds") or {}
        away = entry.get("awayTeamOdds") or {}
        h = home.get("favorite")
        a = away.get("favorite")
        if h and not a:
            return True
        if a and not h:
            return False
    return None


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
        "_favorito_es_local": _favorito_de_odds(comp.get("odds")),
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

# --- Modelo de xG por coordenadas -------------------------------------
# ESPN ubica el arco atacado en x=100, y=50 (porcentaje de largo y ancho).
# Se convierte a metros y se aplica un modelo logistico:
#     xG = sigmoid(B0 + B1 * distancia + B2 * angulo)
# calibrado contra una tabla de xG por distancia/angulo de juego abierto.

LARGO_CANCHA_M = 105.0
ANCHO_CANCHA_M = 68.0
MEDIO_ARCO_M = 3.66          # medio ancho del arco (7.32 m)

XG_B0 = -1.85
XG_B1 = -0.08                # penaliza distancia
XG_B2 = 2.40                 # premia angulo
FACTOR_CABEZAZO = 0.65
XG_PENAL = 0.78
XG_MIN = 0.01
XG_MAX = 0.95

# Un gol de ESPN NO se duplica como "shot-on-target": el play de gol es la
# unica evidencia del tiro, por eso los tipos de gol tambien cuentan como tiro.
TIPOS_TIRO = {
    "shot-on-target",
    "shot-blocked",
    "shot-off-target",
    "shot-hit-woodwork",
    "goal",
    "goal---header",
    "goal---volley",
    "goal---free-kick",
}
TIPOS_PENAL = {"penalty---scored", "penalty---saved", "penalty---missed"}


def _xg_de_tiro(x, y, texto=""):
    """xG de un tiro segun su ubicacion en la cancha."""
    if "penalty" in (texto or "").lower():
        return XG_PENAL

    try:
        dx = (100.0 - float(x)) / 100.0 * LARGO_CANCHA_M
        dy = (float(y) - 50.0) / 100.0 * ANCHO_CANCHA_M
    except (TypeError, ValueError):
        return 0.05

    distancia = max(0.5, math.hypot(dx, dy))
    angulo = abs(math.atan2(MEDIO_ARCO_M - dy, dx) - math.atan2(-MEDIO_ARCO_M - dy, dx))
    z = XG_B0 + XG_B1 * distancia + XG_B2 * angulo
    xg = 1.0 / (1.0 + math.exp(-z))

    if "header" in (texto or "").lower():
        xg *= FACTOR_CABEZAZO

    return max(XG_MIN, min(XG_MAX, xg))


def _minuto_a_segundos(minuto):
    """Convierte '45'+4'' o '67'' a segundos."""
    if minuto is None:
        return None
    limpio = re.sub(r"[^0-9+]", "", str(minuto))
    if not limpio:
        return None
    partes = limpio.split("+")
    try:
        base = int(partes[0])
        extra = int(partes[1]) if len(partes) > 1 and partes[1] else 0
    except (ValueError, IndexError):
        return None
    return (base + extra) * 60


def _mapa_equipos(data):
    """Mapea displayName/shortDisplayName -> 'home'/'away'."""
    mapa = {}
    header = data.get("header", {}) or {}
    comps = header.get("competitions") or [{}]
    for comp in comps:
        for c in comp.get("competitors", []) or []:
            side = c.get("homeAway")
            team = c.get("team") or {}
            for key in ("displayName", "shortDisplayName", "name", "abbreviation", "location"):
                if team.get(key):
                    mapa[team[key]] = side
    return mapa


def _extraer_tiros(data):
    """Lista de tiros (con xG) por equipo desde el play-by-play de ESPN."""
    mapa = _mapa_equipos(data)
    tiros = {"home": [], "away": []}

    for entry in data.get("commentary", []) or []:
        play = entry.get("play") or {}
        tipo = str((play.get("type") or {}).get("type") or "").lower()
        es_penal = tipo in TIPOS_PENAL
        if tipo not in TIPOS_TIRO and not es_penal:
            continue

        side = mapa.get((play.get("team") or {}).get("displayName"))
        if side is None:
            continue

        texto = play.get("text") or ""
        if es_penal:
            xg = XG_PENAL
        else:
            x = play.get("fieldPositionX")
            y = play.get("fieldPositionY")
            xg = 0.05 if x is None or y is None else _xg_de_tiro(x, y, texto)

        reloj = (play.get("clock") or {}).get("value")
        if reloj is None:
            reloj = (entry.get("time") or {}).get("value")

        tiros[side].append({
            "xg": xg,
            "segundo": float(reloj) if reloj is not None else None,
            "tipo": tipo,
        })

    return tiros


def _resumen_tiros(tiros, minuto_actual=None, ventana_reciente=15):
    """Agrega xG total, xG de los ultimos N minutos y grandes ocasiones."""
    res = {
        "xg": {"home": 0.0, "away": 0.0},
        "xg_reciente": {"home": 0.0, "away": 0.0},
        "grandes": {"home": 0, "away": 0},
        "n": {"home": 0, "away": 0},
    }

    limite = None
    seg_actual = _minuto_a_segundos(minuto_actual)
    if seg_actual is not None:
        limite = seg_actual - ventana_reciente * 60

    for side in ("home", "away"):
        for t in tiros.get(side, []):
            res["xg"][side] += t["xg"]
            res["n"][side] += 1
            if t["xg"] >= 0.30:
                res["grandes"][side] += 1
            if limite is not None and t["segundo"] is not None and t["segundo"] >= limite:
                res["xg_reciente"][side] += t["xg"]

    return res


def extraer_xg_y_stats(match_id, liga_slug=None, minuto_actual=None):
    """
    Obtiene xG, stats y favorito en una sola llamada a ESPN summary.

    xG principal: modelo por coordenadas de cada tiro.
    Fallback: estimacion por volumen (SOT/off/blocked) si no hay play-by-play.
    """
    slug = liga_slug or _detectar_slug_para_match(match_id)
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

    tiros = _extraer_tiros(data)
    resumen = _resumen_tiros(tiros, minuto_actual)

    # El play-by-play de ESPN no siempre esta completo (algunas ligas solo
    # publican goles). Si faltan tiros respecto al boxscore, se usa la
    # estimacion por volumen para AMBOS equipos y asi no mezclar escalas.
    tiros_coord = resumen["n"]["home"] + resumen["n"]["away"]
    tiros_box = stats["tiros_totales"]["home"] + stats["tiros_totales"]["away"]
    coords_confiables = tiros_coord > 0 and (
        tiros_box == 0 or tiros_coord >= tiros_box * 0.6
    )

    if coords_confiables:
        xg_home = round(resumen["xg"]["home"], 2)
        xg_away = round(resumen["xg"]["away"], 2)
        stats["fuente_xg"] = "coordenadas"
        stats["xg_reciente"] = {
            "home": round(resumen["xg_reciente"]["home"], 2),
            "away": round(resumen["xg_reciente"]["away"], 2),
        }
        stats["grandes_ocasiones"] = resumen["grandes"]
    else:
        xg_home = _estimar_xg(stats, "home")
        xg_away = _estimar_xg(stats, "away")
        stats["fuente_xg"] = "estimado"
        # Sin play-by-play fiable no hay xG reciente ni grandes ocasiones
        stats["xg_reciente"] = None
        stats["grandes_ocasiones"] = None

    stats["favorito_es_local"] = _extraer_favorito(data)

    return xg_home, xg_away, stats


def _extraer_favorito(data):
    """Favorito real del partido segun las cuotas de ESPN."""
    fav = _favorito_de_odds(data.get("pickcenter"))
    if fav is not None:
        return fav
    return _favorito_de_odds(data.get("odds"))


def _estimar_xg(stats, side):
    """
    Fallback: estima xG desde volumen de tiros cuando no hay coordenadas.
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
            elif name == "redcards":
                stats["tarjetas_rojas"][side] = int(val)
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
        "tarjetas_rojas": {"home": 0, "away": 0},
        "xg_reciente": {"home": 0.0, "away": 0.0},
        "grandes_ocasiones": {"home": 0, "away": 0},
        "favorito_es_local": None,
        "fuente_xg": "sin_datos",
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
