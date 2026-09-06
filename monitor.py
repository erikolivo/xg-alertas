"""
monitor.py
----------
FASE 3 — Vigilancia en vivo de partidos seleccionados.

Cada ciclo (5-15 min adaptativo):
  1. Lee partidos_hoy.json
  2. Para cada partido EN VIVO: obtiene xG + stats de FotMob
  3. Evalua si corresponde alguna alerta (xg_engine.py)
  4. Envia alerta por Telegram si aplica
  5. Guarda snapshot para historial
"""

import json
import time
import datetime
import traceback
from pathlib import Path

from fetch_data import obtener_detalles_partido, extraer_xg, extraer_stats_partido, extraer_eventos
from telegram_utils import enviar_mensaje_telegram, escapar_html
from xg_engine import evaluar_alertas, VENTANA_DEDUPLICACION, MINUTOS_MINIMOS_XG

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"

# Intervalo de vigilancia (segundos)
INTERVALO_BASE = 5 * 60   # 5 minutos
INTERVALO_MAX = 15 * 60   # 15 minutos
DURACION_CICLO = 6 * 60 * 60  # 6 horas max de vigilancia


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
    """Verifica si se envio esta alerta recientemente (misma ventana)."""
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
    """Registra una alerta enviada en el partido."""
    if "alertas_enviadas" not in partido:
        partido["alertas_enviadas"] = []
    partido["alertas_enviadas"].append({
        "tipo": tipo_alerta,
        "minuto": minuto,
        "enviada_en": datetime.datetime.utcnow().isoformat() + "Z",
    })


def _guardar_snapshot(partido, minuto, xg_home, xg_away, goles_home, goles_away, stats):
    """Guarda un snapshot del estado del partido."""
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
    """Agrega las estadisticas del partido al mensaje de alerta."""
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


def _obtener_minuto(partido, detalles):
    """Intenta obtener el minuto actual del partido."""
    try:
        header = detalles.get("header", {})
        status = header.get("status", {})
        score_str = status.get("scoreStr", "")
        # El status de FotMob no siempre trae el minuto directamente
        # Usamos los snapshots para estimar
        snapshots = partido.get("snapshots", [])
        if snapshots:
            ultimo = snapshots[-1]
            return ultimo.get("minuto", "?")
    except Exception:
        pass
    return "?"


def vigilar():
    """Bucle principal de vigilancia."""
    print("[monitor] Iniciando vigilancia de partidos EN VIVO...")

    data = _cargar()
    partidos = data.get("partidos", [])
    if not partidos:
        print("[monitor] No hay partidos para vigilar.")
        return

    print(f"[monitor] {len(partidos)} partido(s) en cola.")

    inicio = time.time()
    intervalo = INTERVALO_BASE

    while (time.time() - inicio) < DURACION_CICLO:
        data = _cargar()
        partidos = data.get("partidos", [])

        partidos_en_vivo = [p for p in partidos if _esta_en_vivo(p)]
        partidos_terminados = [p for p in partidos if _esta_terminado(p)]

        if not partidos_en_vivo:
            print(f"[monitor] No hay partidos en vivo. ({len(partidos_terminados)} terminados)")
            # Si todos terminaron, salir
            if len(partidos_terminados) >= len(partidos):
                print("[monitor] Todos los partidos terminaron. Saliendo.")
                break
            # Esperar mas tiempo si no hay nada en vivo
            time.sleep(INTERVALO_MAX)
            continue

        print(f"[monitor] {len(partidos_en_vivo)} partido(s) en vivo, {len(partidos_terminados)} terminado(s).")

        for partido in partidos_en_vivo:
            try:
                _procesar_partido(partido)
            except Exception as e:
                print(f"[monitor] Error procesando {partido.get('local', '?')} vs {partido.get('visitante', '?')}: {e}")
                traceback.print_exc()

        _guardar(data)

        # Ajustar intervalo segun actividad
        intervalo = _ajustar_intervalo(partidos_en_vivo)
        print(f"[monitor] Proximo ciclo en {intervalo // 60} min...")
        time.sleep(intervalo)

    print("[monitor] Vigilancia finalizada.")


def _esta_en_vivo(partido):
    """Verifica si un partido esta en vivo usando sus snapshots."""
    snapshots = partido.get("snapshots", [])
    resultado = partido.get("resultado_final")
    if resultado:
        return False
    if snapshots:
        ultimo = snapshots[-1]
        # Si hay snapshots recientes, esta en vivo
        return True
    # Sin snapshots, no sabemos -- asumir que no
    return False


def _esta_terminado(partido):
    """Verifica si un partido ya termino."""
    return partido.get("resultado_final") is not None


def _procesar_partido(partido):
    """Procesa un solo partido: obtiene datos, evalua alertas, envia si aplica."""
    match_id = partido["fixture_id"]
    local = partido["local"]
    visitante = partido["visitante"]

    detalles = obtener_detalles_partido(match_id)
    if not detalles:
        print(f"  [!] No se pudieron obtener datos de {local} vs {visitante}")
        return

    # Extraer xG
    xg_home, xg_away = extraer_xg(detalles)
    if xg_home is None:
        xg_home = 0.0
    if xg_away is None:
        xg_away = 0.0

    # Extraer marcador actual
    goles_home = partido.get("_goles_local")
    goles_away = partido.get("_goles_visitante")
    if goles_home is None:
        goles_home = 0
    if goles_away is None:
        goles_away = 0

    # Extraer stats
    stats = extraer_stats_partido(detalles)

    # Extraer minuto (estimado desde snapshots o header)
    minuto = _obtener_minuto(partido, detalles)

    # Guardar snapshot
    _guardar_snapshot(partido, minuto, xg_home, xg_away, goles_home, goles_away, stats)

    # Evaluar alertas
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
        # Verificar deduplicacion
        try:
            minuto_int = int(str(minuto).rstrip("'").split("+")[0])
        except (ValueError, TypeError):
            minuto_int = 0

        if not _ya_se_envio_reciente(partido.get("alertas_enviadas", []), alerta["tipo"], minuto_int):
            # Agregar stats al mensaje
            mensaje_completo = _mensaje_con_stats(alerta, stats, goles_home, goles_away)
            alerta["mensaje"] = mensaje_completo

            # Enviar
            exito = enviar_mensaje_telegram(mensaje_completo)
            if exito:
                _registrar_alerta(partido, alerta["tipo"], minuto)
                print(f"  [ALERTA] {alerta['emoji']} {alerta['tipo']} enviada para {local} vs {visitante}")
        else:
            print(f"  [skip] {alerta['tipo']} ya enviada recientemente para {local} vs {visitante}")

    # Actualizar goles en el partido
    try:
        header = detalles.get("header", {})
        teams = header.get("teams", [])
        if len(teams) >= 2:
            partido["_goles_local"] = teams[0].get("score", goles_home)
            partido["_goles_visitante"] = teams[1].get("score", goles_away)
    except Exception:
        pass


def _ajustar_intervalo(partidos_en_vivo):
    """Ajusta el intervalo segun la cantidad de partidos en vivo."""
    n = len(partidos_en_vivo)
    if n <= 2:
        return INTERVALO_BASE
    elif n <= 5:
        return INTERVALO_BASE * 2
    else:
        return INTERVALO_MAX


if __name__ == "__main__":
    vigilar()
