"""
backtest.py
-----------
Mide la tasa de acierto del motor de alertas sobre partidos ya terminados.

Uso:
    python backtest.py                 # mide con la cache local
    python backtest.py --recolectar    # vuelve a descargar partidos de ESPN

Criterio de acierto: el equipo que dominaba en xG marca antes del final del
partido (el mismo criterio que usa el sistema en vivo). Se separa en
entrenamiento / prueba para detectar sobreajuste.

La cache vive en `data/backtest_cache.json` y contiene, por partido:
  tiros  [[segundo, lado, xg], ...]
  goles  [[segundo, lado], ...]
  rojas  [[segundo, lado], ...]
  favorito_es_local, goles_home, goles_away
"""

import json
import math
import random
import datetime
from pathlib import Path

import requests

from xg_engine import evaluar_alertas

DATA_DIR = Path(__file__).parent / "data"
CACHE = DATA_DIR / "backtest_cache.json"

LIGAS = ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "ned.1", "por.1",
         "bra.1", "arg.1", "ecu.1", "col.1", "tur.1", "bel.1", "sco.1",
         "usa.1", "mex.1"]

TIPOS_TIRO = {
    "shot-on-target", "shot-blocked", "shot-off-target", "shot-hit-woodwork",
    "goal", "goal---header", "goal---volley", "goal---free-kick",
}
TIPOS_PENAL = {"penalty---scored", "penalty---saved", "penalty---missed"}
TIPOS_GOL = {"goal", "goal---header", "goal---volley", "goal---free-kick",
             "penalty---scored"}


# ---------------------------------------------------------------- recoleccion
def _xg_de_tiro(play):
    tipo = str((play.get("type") or {}).get("type") or "").lower()
    if tipo in TIPOS_PENAL:
        return 0.78
    x = play.get("fieldPositionX")
    y = play.get("fieldPositionY")
    if x is None or y is None:
        return 0.05
    dx = (100.0 - float(x)) / 100.0 * 105.0
    dy = (float(y) - 50.0) / 100.0 * 68.0
    dist = max(0.5, math.hypot(dx, dy))
    ang = abs(math.atan2(3.66 - dy, dx) - math.atan2(-3.66 - dy, dx))
    xg = 1.0 / (1.0 + math.exp(-(-1.85 - 0.08 * dist + 2.40 * ang)))
    if "header" in (play.get("text") or "").lower():
        xg *= 0.65
    return max(0.01, min(0.95, xg))


def _procesar_partido(slug, eid, data):
    header = data.get("header", {}).get("competitions", [{}])[0]
    comps = header.get("competitors", [])
    if len(comps) < 2:
        return None

    equipos, score_final = {}, {}
    mapa = {}
    for c in comps:
        side = c.get("homeAway")
        team = c.get("team", {}) or {}
        equipos[side] = team.get("displayName")
        try:
            score_final[side] = int(c.get("score", 0))
        except (TypeError, ValueError):
            score_final[side] = 0
        for key in ("displayName", "shortDisplayName", "name",
                    "abbreviation", "location"):
            if team.get(key):
                mapa[team[key]] = side

    tiros, goles, rojas = [], [], []
    for entry in data.get("commentary", []) or []:
        play = entry.get("play") or {}
        tipo = str((play.get("type") or {}).get("type") or "").lower()
        side = mapa.get((play.get("team") or {}).get("displayName"))
        if side is None:
            continue
        reloj = (play.get("clock") or {}).get("value")
        if reloj is None:
            reloj = (entry.get("time") or {}).get("value")
        if reloj is None:
            continue
        reloj = float(reloj)

        if tipo in TIPOS_TIRO or tipo in TIPOS_PENAL:
            tiros.append([reloj, side, round(_xg_de_tiro(play), 4)])

        if tipo in TIPOS_GOL:
            goles.append([reloj, side])
        elif tipo == "own-goal":
            goles.append([reloj, "away" if side == "home" else "home"])
        elif tipo == "red-card":
            rojas.append([reloj, side])

    tiros.sort(key=lambda x: x[0])
    goles.sort(key=lambda x: x[0])

    rec = {"home": 0, "away": 0}
    for _, side in goles:
        rec[side] += 1
    if rec["home"] != score_final["home"] or rec["away"] != score_final["away"]:
        return None
    if len(tiros) < 5:
        return None

    fav = None
    for entry in (data.get("pickcenter") or []):
        if not isinstance(entry, dict):
            continue
        h = (entry.get("homeTeamOdds") or {}).get("favorite")
        a = (entry.get("awayTeamOdds") or {}).get("favorite")
        if h and not a:
            fav = True
            break
        if a and not h:
            fav = False
            break

    return {
        "fixture_id": eid,
        "liga": slug,
        "local": equipos.get("home"),
        "visitante": equipos.get("away"),
        "goles_home": score_final["home"],
        "goles_away": score_final["away"],
        "favorito_es_local": fav,
        "tiros": tiros,
        "goles": goles,
        "rojas": rojas,
    }


def recolectar(dias=12, max_por_liga=16):
    """Descarga partidos terminados de ESPN y rellena la cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    hoy = datetime.date.today()
    total = 0
    for slug in LIGAS:
        encontrados = 0
        for delta in range(dias):
            fecha = (hoy - datetime.timedelta(days=delta)).strftime("%Y%m%d")
            try:
                sb = requests.get(
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}"
                    f"/scoreboard?dates={fecha}", timeout=20,
                ).json()
            except Exception:
                continue

            for ev in sb.get("events", []):
                if encontrados >= max_por_liga:
                    break
                comp = ev.get("competitions", [{}])[0]
                if comp.get("status", {}).get("type", {}).get("state") != "post":
                    continue
                eid = str(ev["id"])
                if eid in cache:
                    encontrados += 1
                    continue
                try:
                    data = requests.get(
                        f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                        f"{slug}/summary?event={eid}", timeout=20,
                    ).json()
                except Exception:
                    continue
                reg = _procesar_partido(slug, eid, data)
                if reg:
                    cache[eid] = reg
                    encontrados += 1
            if encontrados >= max_por_liga:
                break
        print(f"  {slug}: {encontrados} partidos")
        total += encontrados

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"Cache: {len(cache)} partidos -> {CACHE}")


# ---------------------------------------------------------------- medicion
def _estado(partido, minuto):
    seg = minuto * 60
    xg = {"home": 0.0, "away": 0.0}
    xg_rec = {"home": 0.0, "away": 0.0}
    sot = {"home": 0, "away": 0}
    total = {"home": 0, "away": 0}
    grandes = {"home": 0, "away": 0}
    for t, side, x in partido["tiros"]:
        if t <= seg:
            xg[side] += x
            total[side] += 1
            if x >= 0.30:
                grandes[side] += 1
            if x >= 0.10:
                sot[side] += 1
            if t > seg - 15 * 60:
                xg_rec[side] += x

    gh = ga = 0
    for t, side in partido["goles"]:
        if t <= seg:
            if side == "home":
                gh += 1
            else:
                ga += 1

    rojas = {"home": 0, "away": 0}
    for t, side in partido["rojas"]:
        if t <= seg:
            rojas[side] += 1

    return xg, xg_rec, sot, total, grandes, gh, ga, rojas


def _marca_final(partido, minuto, dom):
    seg = minuto * 60
    return any(t > seg and s == dom for t, s in partido["goles"])


def _prox_es_dom(partido, minuto, dom):
    seg = minuto * 60
    for t, s in partido["goles"]:
        if t > seg:
            return s == dom
    return None


def correr(partidos, paso=5):
    """Replay del motor de produccion, con la misma deduplicacion del monitor."""
    alertas = []
    for p in partidos:
        enviadas = []
        for minuto in range(15, 81, paso):
            xg, xg_rec, sot, total, grandes, gh, ga, rojas = _estado(p, minuto)
            contexto = {
                "rojas_home": rojas["home"], "rojas_away": rojas["away"],
                "tiros_puerta_home": sot["home"], "tiros_puerta_away": sot["away"],
                "tiros_totales_home": total["home"],
                "tiros_totales_away": total["away"],
                "grandes_home": grandes["home"], "grandes_away": grandes["away"],
            }
            alerta = evaluar_alertas(
                xg_home=round(xg["home"], 2), xg_away=round(xg["away"], 2),
                goles_home=gh, goles_away=ga, minuto=f"{minuto}'",
                favorito_es_local=p["favorito_es_local"],
                xg_reciente={"home": round(xg_rec["home"], 2),
                             "away": round(xg_rec["away"], 2)},
                contexto=contexto,
            )
            if not alerta:
                continue

            diff = abs(xg["home"] - xg["away"])
            if any(
                minuto - prev["minuto"] < 6
                or (prev["tipo"] == alerta["tipo"]
                    and minuto - prev["minuto"] < 15
                    and diff - prev["diff"] < 0.30)
                or (minuto - prev["minuto"] < 10 and diff - prev["diff"] < 0.30)
                for prev in enviadas
            ):
                continue

            dom = "home" if xg["home"] > xg["away"] else "away"
            alertas.append({
                "tipo": alerta["tipo"], "minuto": minuto,
                "final": _marca_final(p, minuto, dom),
                "prox": _prox_es_dom(p, minuto, dom),
            })
            enviadas.append({"tipo": alerta["tipo"], "minuto": minuto,
                             "diff": diff})
    return alertas


def _resumen(nombre, alertas, n_partidos):
    if not alertas:
        print(f"  {nombre:6s} 0 alertas")
        return
    fin = sum(a["final"] for a in alertas) / len(alertas) * 100
    con = [a for a in alertas if a["prox"] is not None]
    prox = sum(1 for a in con if a["prox"]) / len(con) * 100 if con else 0
    print(f"  {nombre:6s} {len(alertas):4d} alertas "
          f"({len(alertas) / n_partidos:.2f}/partido)  "
          f"ACIERTO {fin:5.1f}%  proxGol {prox:5.1f}%")


def medir():
    if not CACHE.exists():
        print(f"No existe {CACHE}. Corre: python backtest.py --recolectar")
        return

    partidos = list(json.loads(CACHE.read_text(encoding="utf-8")).values())
    random.Random(42).shuffle(partidos)
    mitad = len(partidos) // 2
    train, test = partidos[:mitad], partidos[mitad:]

    print(f"Partidos en la cache: {len(partidos)}")
    print("\n=== Validacion train / test ===")
    _resumen("TRAIN", correr(train), len(train))
    _resumen("TEST", correr(test), len(test))
    _resumen("TODO", correr(partidos), len(partidos))

    alertas = correr(partidos)
    por_tipo = {}
    for a in alertas:
        por_tipo.setdefault(a["tipo"], []).append(a)
    if por_tipo:
        print("\n=== Por tipo ===")
        for tipo, lst in sorted(por_tipo.items(), key=lambda x: -len(x[1])):
            fin = sum(a["final"] for a in lst) / len(lst) * 100
            con = [a for a in lst if a["prox"] is not None]
            prox = sum(1 for a in con if a["prox"]) / len(con) * 100 if con else 0
            print(f"  {tipo:16s} {len(lst):4d}  ACIERTO {fin:5.1f}%  "
                  f"proxGol {prox:5.1f}%")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if "--recolectar" in sys.argv:
        recolectar()
    medir()
