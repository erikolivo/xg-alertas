"""
alertas_log.py
--------------
Registro persistente (append-only) de alertas enviadas y resueltas.

Por que existe: la Fase 1 reescribe `data/partidos_hoy.json` varias veces al
dia. Sin este log, todo el historial de aciertos se perdia y no se podia
calibrar el sistema. El log vive en `data/alertas_log.jsonl` y nunca se borra.
"""

import json
import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO = DATA_DIR / "alertas_log.jsonl"


def _ahora():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _escribir(registro):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def registrar_envio(partido, alerta, minuto, xg_home, xg_away,
                    goles_home, goles_away, stats=None):
    """Guarda una alerta en el momento en que se envia."""
    stats = stats or {}
    try:
        diff = float(xg_home or 0) - float(xg_away or 0)
    except (TypeError, ValueError):
        diff = 0.0

    _escribir({
        "evento": "envio",
        "timestamp": _ahora(),
        "fixture_id": partido.get("fixture_id"),
        "liga": partido.get("liga"),
        "liga_slug": partido.get("liga_slug"),
        "local": partido.get("local"),
        "visitante": partido.get("visitante"),
        "favorito_es_local": partido.get("favorito_es_local"),
        "tipo": alerta.get("tipo"),
        "emoji": alerta.get("emoji"),
        "minuto": minuto,
        "xg_home": xg_home,
        "xg_away": xg_away,
        "xg_diff": round(abs(diff), 2),
        "equipo_dominante": "home" if diff >= 0 else "away",
        "goles_home_envio": goles_home,
        "goles_away_envio": goles_away,
        "grandes_ocasiones": stats.get("grandes_ocasiones"),
        "xg_reciente": stats.get("xg_reciente"),
        "fuente_xg": stats.get("fuente_xg"),
    })


def registrar_resolucion(partido, alerta, goles_home, goles_away, resultado):
    """Guarda el desenlace (acierto / fallo) de una alerta."""
    _escribir({
        "evento": "resolucion",
        "timestamp": _ahora(),
        "fixture_id": partido.get("fixture_id"),
        "liga": partido.get("liga"),
        "liga_slug": partido.get("liga_slug"),
        "local": partido.get("local"),
        "visitante": partido.get("visitante"),
        "tipo": alerta.get("tipo"),
        "minuto": alerta.get("minuto"),
        "equipo_dominante": alerta.get("equipo_dominante"),
        "xg_diff": alerta.get("xg_diff_envio"),
        "goles_home_envio": alerta.get("goles_home_envio"),
        "goles_away_envio": alerta.get("goles_away_envio"),
        "goles_home_final": goles_home,
        "goles_away_final": goles_away,
        "resultado": resultado,
        "acierto": resultado == "acierto",
    })


def leer_todo():
    """Devuelve todos los registros del log (lista de dicts)."""
    if not ARCHIVO.exists():
        return []
    registros = []
    for linea in ARCHIVO.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            registros.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return registros


def _clave(reg):
    return (reg.get("fixture_id"), reg.get("tipo"), reg.get("minuto"))


def resumen():
    """
    Empareja envios con resoluciones y devuelve estadisticas de acierto.

    Devuelve un dict con:
      enviadas, resueltas, aciertos, fallos, tasa, por_tipo, por_minuto
    """
    regs = leer_todo()
    envios = [r for r in regs if r.get("evento") == "envio"]
    resoluciones = {}
    for r in regs:
        if r.get("evento") == "resolucion":
            resoluciones[_clave(r)] = r

    aciertos = fallos = 0
    por_tipo = {}
    por_minuto = {}

    for env in envios:
        res = resoluciones.get(_clave(env))
        if not res:
            continue
        ok = bool(res.get("acierto"))
        aciertos += 1 if ok else 0
        fallos += 0 if ok else 1

        tipo = env.get("tipo") or "?"
        t = por_tipo.setdefault(tipo, {"n": 0, "aciertos": 0})
        t["n"] += 1
        t["aciertos"] += 1 if ok else 0

        try:
            m = int(str(env.get("minuto", "0")).replace("'", "").split("+")[0])
            rango = f"{(m // 10) * 10:02d}-{(m // 10) * 10 + 9:02d}"
        except (TypeError, ValueError):
            rango = "?"
        pm = por_minuto.setdefault(rango, {"n": 0, "aciertos": 0})
        pm["n"] += 1
        pm["aciertos"] += 1 if ok else 0

    resueltas = aciertos + fallos
    return {
        "enviadas": len(envios),
        "resueltas": resueltas,
        "aciertos": aciertos,
        "fallos": fallos,
        "tasa": round(aciertos / resueltas * 100, 1) if resueltas else 0.0,
        "por_tipo": por_tipo,
        "por_minuto": por_minuto,
    }


def formatear_resumen(datos=None):
    """Texto legible del resumen para Telegram."""
    d = datos or resumen()
    if not d["resueltas"]:
        return "Sin alertas resueltas todavia."

    lineas = [
        "📊 <b>Rendimiento de alertas</b>",
        "",
        f"Enviadas: {d['enviadas']}  |  Resueltas: {d['resueltas']}",
        f"✅ Aciertos: {d['aciertos']}   ❌ Fallos: {d['fallos']}",
        f"<b>Tasa de acierto: {d['tasa']}%</b>",
    ]
    if d["por_tipo"]:
        lineas.append("")
        lineas.append("<b>Por tipo</b>")
        for tipo, v in sorted(d["por_tipo"].items(), key=lambda x: -x[1]["n"]):
            pct = round(v["aciertos"] / v["n"] * 100, 1) if v["n"] else 0
            lineas.append(f"  {tipo}: {v['aciertos']}/{v['n']} ({pct}%)")
    return "\n".join(lineas)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    d = resumen()
    print(f"Archivo: {ARCHIVO}")
    print(f"Enviadas: {d['enviadas']}  |  Resueltas: {d['resueltas']}")
    print(f"Aciertos: {d['aciertos']}  |  Fallos: {d['fallos']}  |  Tasa: {d['tasa']}%")
    if d["por_tipo"]:
        print("\nPor tipo:")
        for tipo, v in sorted(d["por_tipo"].items(), key=lambda x: -x[1]["n"]):
            pct = round(v["aciertos"] / v["n"] * 100, 1) if v["n"] else 0
            print(f"  {tipo:16s} {v['aciertos']:3d}/{v['n']:3d}  ({pct}%)")
    if d["por_minuto"]:
        print("\nPor minuto:")
        for rango, v in sorted(d["por_minuto"].items()):
            pct = round(v["aciertos"] / v["n"] * 100, 1) if v["n"] else 0
            print(f"  {rango}  {v['aciertos']:3d}/{v['n']:3d}  ({pct}%)")
