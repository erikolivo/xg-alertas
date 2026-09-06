"""
monitor.py
----------
FASE 3 — Vigilancia en vivo de partidos seleccionados.

Todo via ESPN: fixtures, xG, stats, marcador.
"""

import json
import time
import datetime
import traceback
from pathlib import Path

from fetch_data import extraer_xg_y_stats, obtener_score_en_vivo, LIGAS_ESPN
from telegram_utils import enviar_mensaje_telegram
from xg_engine import evaluar_alertas, VENTANA_DEDUPLICACION

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"

INTERVALO_BASE = 5 * 60
INTERVALO_MAX = 15 * 60
DURACION_CICLO = 6 * 60 * 60


def _cargar():
    if not ARCHIVO_PARTIDOS.exists():
        return {"partidos": []}
    try:
        return json.loads(ARCHIVO_PARTIDOS.read_text(encoding="utf-8"))
    except Exception:
        return {"partidos": []}


def _guardar(data):
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVO_PARTIDOS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ya_se_envio_reciente(alertas_enviadas, tipo_alerta, minuto_actual):
    for a in reversed(alertas_enviadas):
        if a.get("tipo") == tipo_alerta:
            try:
                minuto_anterior = int(str(a.get("minuto", "0")).rstrip("'").split("+")[0])
                if abs(minuto_actual - minuto_anterior) < VENTANA_DEDUPLICACION:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def _registrar_alerta(partido, tipo_alerta, minuto):
    if "alertas_enviadas" not in partido:
        partido["alertas_enviadas"] = []
    partido["alertas_enviadas"].append({
        "tipo": tipo_alerta,
        "minuto": minuto,
        "enviada_en": datetime.datetime.utcnow().isoformat() + "Z",
    })


def _guardar_snapshot(partido, minuto, xg_home, xg_away, goles_home, goles_away, stats):
    if "snapshots" not in partido:
        partido["snapshots"] = []
    partido["snapshots"].append({
        "minuto": minuto,
        "xg_home": xg_home,
        "xg_away": xg_away,
        "goles_home": goles_home,
        "goles_away": goles_away,
        "stats": stats,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })


def _mensaje_con_stats(alerta, stats, goles_home, goles_away):
    lineas = [alerta["mensaje"]]

    if stats:
        lineas.append("")
        lineas.append("━━━ Estadísticas ━━━")
        p = stats.get("posesion", {})
        t = stats.get("tiros_totales", {})
        tp = stats.get("tiros_puerta", {})
        c = stats.get("corners", {})
        f = stats.get("faltas", {})

        def _fmt(v):
            return str(v) if v else "0"

        lineas.append(f"Posesión:       {_fmt(p.get('home', 50))}% vs {_fmt(p.get('away', 50))}%")
        lineas.append(f"Tiros totales:  {_fmt(t.get('home', 0))} vs {_fmt(t.get('away', 0))}")
        lineas.append(f"Tiros a puerta: {_fmt(tp.get('home', 0))} vs {_fmt(tp.get('away', 0))}")
        lineas.append(f"Corners:        {_fmt(c.get('home', 0))} vs {_fmt(c.get('away', 0))}")
        lineas.append(f"Faltas:         {_fmt(f.get('home', 0))} vs {_fmt(f.get('away', 0))}")

    lineas.append("")
    lineas.append(f"⚽ Marcador real: {goles_home} - {goles_away}")

    return "\n".join(lineas)


def _esta_en_vivo(partido):
    resultado = partido.get("resultado_final")
    if resultado:
        return False
    return True


def _esta_terminado(partido):
    return partido.get("resultado_final") is not None


def _procesar_partido(partido):
    match_id = partido["fixture_id"]
    local = partido["local"]
    visitante = partido["visitante"]
    liga_slug = partido.get("liga_slug", "")

    # 1. Obtener marcador en vivo desde ESPN
    goles_home, goles_away, estado = obtener_score_en_vivo(match_id, liga_slug)

    if estado is None:
        print(f"  [!] No se pudieron obtener datos de {local} vs {visitante}")
        return

    if estado == "post":
        if goles_home is not None and goles_away is not None:
            partido["resultado_final"] = {
                "goles_home": goles_home,
                "goles_away": goles_away,
            }
        return

    if estado != "in":
        return

    # 2. Obtener xG y stats desde ESPN
    xg_home, xg_away, stats = extraer_xg_y_stats(match_id)
    if xg_home is None:
        xg_home = 0.0
    if xg_away is None:
        xg_away = 0.0
    if goles_home is None:
        goles_home = 0
    if goles_away is None:
        goles_away = 0

    # 3. Minuto estimado desde snapshots
    minuto = "?"
    snapshots = partido.get("snapshots", [])
    if snapshots:
        minuto = snapshots[-1].get("minuto", "?")

    # 4. Guardar snapshot
    _guardar_snapshot(partido, minuto, xg_home, xg_away, goles_home, goles_away, stats)

    # 5. Evaluar alertas
    favorito_es_local = partido.get("favorito_es_local")
    if favorito_es_local is None:
        favorito_es_local = True

    alerta = evaluar_alertas(
        xg_home=xg_home, xg_away=xg_away,
        goles_home=goles_home, goles_away=goles_away,
        minuto=minuto,
        favorito_es_local=favorito_es_local,
        historial_alertas=partido.get("alertas_enviadas", []),
    )

    if alerta:
        try:
            minuto_int = int(str(minuto).rstrip("'").split("+")[0])
        except (ValueError, TypeError):
            minuto_int = 0

        if not _ya_se_envio_reciente(partido.get("alertas_enviadas", []), alerta["tipo"], minuto_int):
            mensaje_completo = _mensaje_con_stats(alerta, stats, goles_home, goles_away)
            alerta["mensaje"] = mensaje_completo

            exito = enviar_mensaje_telegram(mensaje_completo)
            if exito:
                _registrar_alerta(partido, alerta["tipo"], minuto)
                print(f"  [ALERTA] {alerta['emoji']} {alerta['tipo']} enviada para {local} vs {visitante}")
        else:
            print(f"  [skip] {alerta['tipo']} ya enviada recientemente para {local} vs {visitante}")


def _ajustar_intervalo(partidos_en_vivo):
    n = len(partidos_en_vivo)
    if n <= 2:
        return INTERVALO_BASE
    elif n <= 5:
        return INTERVALO_BASE * 2
    else:
        return INTERVALO_MAX


def vigilar():
    print("[monitor] Iniciando vigilancia de partidos EN VIVO (ESPN)...")

    data = _cargar()
    partidos = data.get("partidos", [])
    if not partidos:
        print("[monitor] No hay partidos para vigilar.")
        return

    print(f"[monitor] {len(partidos)} partido(s) en cola.")

    inicio = time.time()

    while (time.time() - inicio) < DURACION_CICLO:
        data = _cargar()
        partidos = data.get("partidos", [])

        partidos_activos = [p for p in partidos if not _esta_terminado(p)]
        partidos_terminados = [p for p in partidos if _esta_terminado(p)]

        if not partidos_activos:
            print(f"[monitor] No hay partidos activos. ({len(partidos_terminados)} terminados)")
            if len(partidos_terminados) >= len(partidos):
                print("[monitor] Todos los partidos terminaron. Saliendo.")
                break
            time.sleep(INTERVALO_MAX)
            continue

        print(f"[monitor] {len(partidos_activos)} partido(s) activo(s), {len(partidos_terminados)} terminado(s).")

        for partido in partidos_activos:
            try:
                _procesar_partido(partido)
            except Exception as e:
                print(f"[monitor] Error: {partido.get('local', '?')} vs {partido.get('visitante', '?')}: {e}")
                traceback.print_exc()

        _guardar(data)

        intervalo = _ajustar_intervalo(partidos_activos)
        print(f"[monitor] Proximo ciclo en {intervalo // 60} min...")
        time.sleep(intervalo)

    print("[monitor] Vigilancia finalizada.")


if __name__ == "__main__":
    vigilar()
