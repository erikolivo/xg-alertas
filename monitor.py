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
from xg_engine import (
    evaluar_alertas,
    VENTANA_DEDUPLICACION,
    UMBRAL_XG_FUERTE,
    UMBRAL_XG_DOMINANCIA,
)

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"

INTERVALO_BASE = 5 * 60
INTERVALO_MAX = 15 * 60
DURACION_CICLO = 3 * 60 * 60

# Anti-repeticion: no repetir ninguna alerta del mismo partido antes de N min
# salvo que el xG haya crecido de forma clara.
VENTANA_MINIMA_ENTRE_ALERTAS = 10
AUMENTO_XG_PARA_REALERTAR = 0.30
# Nunca dos alertas del mismo partido mas cerca que esto, pase lo que pase
VENTANA_MINIMA_ABSOLUTA = 6


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


def _minuto_a_int(minuto):
    """Convierte '45'+4' o '52' a entero limpio."""
    import re
    try:
        limpio = re.sub(r"[^0-9+]", "", str(minuto))
        return int(limpio.split("+")[0]) if limpio else 0
    except (ValueError, TypeError):
        return 0


def _debe_enviar_alerta(alertas_enviadas, tipo_alerta, minuto_actual, xg_diff):
    """
    Evita repetir alertas del mismo partido.

    - Mismo tipo: bloquea dentro de VENTANA_DEDUPLICACION minutos.
    - Cualquier tipo: bloquea dentro de VENTANA_MINIMA_ENTRE_ALERTAS minutos.
    - En ambos casos deja pasar si el xG crecio >= AUMENTO_XG_PARA_REALERTAR.
    """
    for a in reversed(alertas_enviadas):
        minuto_anterior = _minuto_a_int(a.get("minuto", "0"))
        delta_min = abs(minuto_actual - minuto_anterior)

        if delta_min < VENTANA_MINIMA_ABSOLUTA:
            return False

        try:
            diff_anterior = abs(float(a.get("xg_diff_envio") or 0))
        except (TypeError, ValueError):
            diff_anterior = 0.0
        crecimiento = abs(xg_diff) - diff_anterior

        if a.get("tipo") == tipo_alerta:
            if delta_min < VENTANA_DEDUPLICACION and crecimiento < AUMENTO_XG_PARA_REALERTAR:
                return False
        else:
            if delta_min < VENTANA_MINIMA_ENTRE_ALERTAS and crecimiento < AUMENTO_XG_PARA_REALERTAR:
                return False

    return True


def _confirmar_con_lectura_previa(partido, xg_diff):
    """
    Confirmacion: una senal marginal debe repetirse en dos lecturas seguidas.
    Una dominancia fuerte (>= UMBRAL_XG_FUERTE) se envia de inmediato.
    """
    if abs(xg_diff) >= UMBRAL_XG_FUERTE:
        return True

    snapshots = partido.get("snapshots") or []
    if not snapshots:
        return True

    previo = snapshots[-1]
    try:
        diff_previo = float(previo.get("xg_home", 0)) - float(previo.get("xg_away", 0))
    except (TypeError, ValueError):
        return True

    mismo_lado = (diff_previo * xg_diff) > 0
    return abs(diff_previo) >= UMBRAL_XG_DOMINANCIA and mismo_lado


def _registrar_alerta(partido, alerta, minuto, xg_home, xg_away,
                      goles_home, goles_away, stats=None):
    if "alertas_enviadas" not in partido:
        partido["alertas_enviadas"] = []

    stats = stats or {}
    xg_diff = (float(xg_home or 0) - float(xg_away or 0))
    equipo_dominante = "home" if xg_diff >= 0 else "away"

    partido["alertas_enviadas"].append({
        "tipo": alerta["tipo"],
        "emoji": alerta.get("emoji", ""),
        "minuto": minuto,
        "xg_home": xg_home,
        "xg_away": xg_away,
        "xg_diff_envio": round(abs(xg_diff), 2),
        "equipo_dominante": equipo_dominante,
        "grandes_ocasiones": stats.get("grandes_ocasiones"),
        "goles_home_envio": goles_home,
        "goles_away_envio": goles_away,
        "enviada_en": datetime.datetime.utcnow().isoformat() + "Z",
        "resuelta": False,
        "resultado": None,
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


def _mensaje_con_stats(alerta, stats, goles_home, goles_away, local, visitante):
    lineas = alerta["mensaje"].split("\n")

    # Insertar nombres de equipos despues del encabezado y el tipo de alerta
    lineas.insert(1, f"{local} vs {visitante}")

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
    lineas.append(f"⚽ {local} {goles_home} - {goles_away} {visitante}")

    return "\n".join(lineas)


def _esta_en_vivo(partido):
    resultado = partido.get("resultado_final")
    if resultado:
        return False
    return True


def _esta_terminado(partido):
    return partido.get("resultado_final") is not None


def _verificar_alertas(partido, goles_home, goles_away, estado):
    """
    Verifica si las alertas enviadas resultaron en gol del equipo dominante.
    - Dominante marcó -> ACIERTO
    - Rival marcó o terminó sin gol del dominante -> FALLO
    """
    alertas = partido.get("alertas_enviadas", [])
    sin_resolver = [a for a in alertas if not a.get("resuelta")]
    if not sin_resolver:
        return

    local = partido.get("local", "?")
    visitante = partido.get("visitante", "?")
    partido_terminado = estado == "post"

    for alerta in sin_resolver:
        dom = alerta.get("equipo_dominante", "home")
        goles_envio_h = alerta.get("goles_home_envio", 0)
        goles_envio_a = alerta.get("goles_away_envio", 0)

        if dom == "home":
            equipo_dom = local
            equipo_rival = visitante
            marcó_dominante = goles_home > goles_envio_h
            marco_rival = goles_away > goles_envio_a
        else:
            equipo_dom = visitante
            equipo_rival = local
            marcó_dominante = goles_away > goles_envio_a
            marco_rival = goles_home > goles_envio_h

        # Caso 1: Dominante marcó -> ACIERTO
        if marcó_dominante:
            alerta["resuelta"] = True
            alerta["resultado"] = "acierto"
            msg = (
                f"✅ ACIERTO\n"
                f"{equipo_dom} marcó gol después de la alerta\n"
                f"Alerta: {alerta['tipo']} (min {alerta['minuto']})\n"
                f"⚽ {local} {goles_home} - {goles_away} {visitante}"
            )
            enviar_mensaje_telegram(msg)
            print(f"    [ACIERTO] {alerta['tipo']} -> {equipo_dom} marcó")
            continue

        # Caso 2: Rival marcó -> FALLO
        if marco_rival:
            alerta["resuelta"] = True
            alerta["resultado"] = "fallo_rival"
            msg = (
                f"❌ FALLO\n"
                f"{equipo_rival} (rival) marcó gol\n"
                f"Alerta: {alerta['tipo']} (min {alerta['minuto']})\n"
                f"⚽ {local} {goles_home} - {goles_away} {visitante}"
            )
            enviar_mensaje_telegram(msg)
            print(f"    [FALLO] {alerta['tipo']} -> {equipo_rival} (rival) marcó")
            continue

        # Caso 3: Partido terminó sin gol del dominante -> FALLO
        if partido_terminado:
            alerta["resuelta"] = True
            alerta["resultado"] = "fallo_sin_gol"
            msg = (
                f"❌ FALLO\n"
                f"Partido terminó sin gol de {equipo_dom}\n"
                f"Alerta: {alerta['tipo']} (min {alerta['minuto']})\n"
                f"⚽ {local} {goles_home} - {goles_away} {visitante}"
            )
            enviar_mensaje_telegram(msg)
            print(f"    [FALLO] {alerta['tipo']} -> sin gol de {equipo_dom}")


def _procesar_partido(partido):
    match_id = partido["fixture_id"]
    local = partido["local"]
    visitante = partido["visitante"]
    liga_slug = partido.get("liga_slug", "")

    # 1. Obtener marcador en vivo desde ESPN
    goles_home, goles_away, estado, clock = obtener_score_en_vivo(match_id, liga_slug)

    if estado is None:
        print(f"  [!] {local} vs {visitante}: sin datos ESPN")
        return

    if estado == "post":
        if goles_home is not None and goles_away is not None:
            # Verificar si las alertas existentes resultaron en gol
            _verificar_alertas(partido, goles_home, goles_away, "post")
            partido["resultado_final"] = {
                "goles_home": goles_home,
                "goles_away": goles_away,
            }
        return

    if estado != "in":
        return

    print(f"  [LIVE] {local} vs {visitante} ({clock}) - {goles_home}-{goles_away}")

    # Verificar si alertas previas se resolvieron (gol del dominante o rival)
    if goles_home is not None and goles_away is not None:
        _verificar_alertas(partido, goles_home, goles_away, "in")

    # 2. Obtener xG real (coordenadas) y stats desde ESPN
    xg_home, xg_away, stats = extraer_xg_y_stats(
        match_id, liga_slug=liga_slug, minuto_actual=clock
    )
    if xg_home is None:
        xg_home = 0.0
    if xg_away is None:
        xg_away = 0.0
    if goles_home is None:
        goles_home = 0
    if goles_away is None:
        goles_away = 0

    print(f"    xG: {xg_home:.2f} vs {xg_away:.2f} (diff: {xg_home - xg_away:+.2f}) "
          f"[{stats.get('fuente_xg', '?')}]")

    # 3. Minuto desde el reloj de ESPN
    minuto = clock if clock else "?"
    minuto_int = _minuto_a_int(minuto)

    # 4. Favorito real (cuotas ESPN) si Fase 1 no lo trajo
    if partido.get("favorito_es_local") is None:
        favorito = stats.get("favorito_es_local")
        if favorito is not None:
            partido["favorito_es_local"] = favorito
            partido["favorito"] = local if favorito else visitante

    # 5. Contexto para el motor
    rojas = stats.get("tarjetas_rojas", {}) or {}
    puerta = stats.get("tiros_puerta", {}) or {}
    totales = stats.get("tiros_totales", {}) or {}
    grandes = stats.get("grandes_ocasiones") or {}

    contexto = {
        "rojas_home": rojas.get("home", 0),
        "rojas_away": rojas.get("away", 0),
        "tiros_puerta_home": puerta.get("home", 0),
        "tiros_puerta_away": puerta.get("away", 0),
        "tiros_totales_home": totales.get("home", 0),
        "tiros_totales_away": totales.get("away", 0),
        "grandes_home": grandes.get("home"),
        "grandes_away": grandes.get("away"),
    }

    # 6. Evaluar alertas
    alerta = evaluar_alertas(
        xg_home=xg_home, xg_away=xg_away,
        goles_home=goles_home, goles_away=goles_away,
        minuto=minuto,
        favorito_es_local=partido.get("favorito_es_local"),
        historial_alertas=partido.get("alertas_enviadas", []),
        xg_reciente=stats.get("xg_reciente"),
        contexto=contexto,
    )

    xg_diff = xg_home - xg_away

    if alerta:
        if not _confirmar_con_lectura_previa(partido, xg_diff):
            print(f"  [skip] {alerta['tipo']}: sin confirmacion en la lectura previa")
        elif not _debe_enviar_alerta(partido.get("alertas_enviadas", []),
                                     alerta["tipo"], minuto_int, xg_diff):
            print(f"  [skip] {alerta['tipo']} repetida para {local} vs {visitante}")
        else:
            mensaje_completo = _mensaje_con_stats(
                alerta, stats, goles_home, goles_away, local, visitante
            )
            alerta["mensaje"] = mensaje_completo

            if enviar_mensaje_telegram(mensaje_completo):
                _registrar_alerta(
                    partido, alerta, minuto, xg_home, xg_away,
                    goles_home, goles_away, stats,
                )
                print(f"  [ALERTA] {alerta['emoji']} {alerta['tipo']} enviada para {local} vs {visitante}")

    # 7. Guardar snapshot (despues de evaluar: la confirmacion usa la lectura previa)
    _guardar_snapshot(partido, minuto, xg_home, xg_away, goles_home, goles_away, stats)


def _ajustar_intervalo(partidos_en_vivo):
    n = len(partidos_en_vivo)
    if n <= 2:
        return INTERVALO_BASE
    elif n <= 5:
        return INTERVALO_BASE * 2
    else:
        return INTERVALO_MAX


def _hora_utc_a_minutos(hora_utc):
    """Convierte 'HH:MM' a minutos desde medianoche UTC."""
    if not hora_utc:
        return None
    try:
        h, m = hora_utc.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _partido_ya_empezo(partido):
    """Verifica si el partido ya empezo segun su hora_utc."""
    hora_utc = partido.get("hora_utc", "")
    if not hora_utc:
        return True  # Sin hora, asumir que ya empezo

    ahora = datetime.datetime.now(datetime.timezone.utc)
    minutos_ahora = ahora.hour * 60 + ahora.minute
    minutos_partido = _hora_utc_a_minutos(hora_utc)

    if minutos_partido is None:
        return True

    # Empezo si ya paso la hora (con margen de -30 min para precalentar)
    return minutos_ahora >= (minutos_partido - 30)


def _partido_en_ventana(partido):
    """Verifica si el partido esta en ventana de +/- 2 horas de su hora."""
    hora_utc = partido.get("hora_utc", "")
    if not hora_utc:
        return True

    ahora = datetime.datetime.now(datetime.timezone.utc)
    minutos_ahora = ahora.hour * 60 + ahora.minute
    minutos_partido = _hora_utc_a_minutos(hora_utc)

    if minutos_partido is None:
        return True

    # Ventana: desde 30 min antes hasta 2.5 horas despues
    return (minutos_ahora >= minutos_partido - 30) and (minutos_partido <= minutos_ahora + 150)


def vigilar():
    print("[monitor] Iniciando vigilancia de partidos EN VIVO (ESPN)...")

    data = _cargar()
    partidos = data.get("partidos", [])
    if not partidos:
        print("[monitor] No hay partidos para vigilar.")
        return

    # Filtrar: solo partidos con slug valido (no "all")
    partidos = [p for p in partidos if p.get("liga_slug") and p.get("liga_slug") != "all"]
    print(f"[monitor] {len(partidos)} partido(s) con slug valido.")

    if not partidos:
        print("[monitor] No hay partidos con slug valido. Saliendo.")
        return

    inicio = time.time()

    while (time.time() - inicio) < DURACION_CICLO:
        data = _cargar()
        partidos = data.get("partidos", [])
        partidos = [p for p in partidos if p.get("liga_slug") and p.get("liga_slug") != "all"]

        # Filtrar: solo partidos en ventana y no terminados
        partidos_en_ventana = [p for p in partidos if _partido_en_ventana(p) and not _esta_terminado(p)]
        partidos_terminados = [p for p in partidos if _esta_terminado(p)]

        if not partidos_en_ventana:
            print(f"[monitor] No hay partidos en ventana. ({len(partidos_terminados)} terminados)")
            if len(partidos_terminados) >= len(partidos):
                print("[monitor] Todos los partidos terminaron. Saliendo.")
                break
            time.sleep(INTERVALO_MAX)
            continue

        print(f"[monitor] {len(partidos_en_ventana)} partido(s) en ventana, {len(partidos_terminados)} terminado(s).")

        for partido in partidos_en_ventana:
            try:
                _procesar_partido(partido)
            except Exception as e:
                print(f"[monitor] Error: {partido.get('local', '?')} vs {partido.get('visitante', '?')}: {e}")
                traceback.print_exc()

        _guardar(data)

        intervalo = _ajustar_intervalo(partidos_en_ventana)
        print(f"[monitor] Proximo ciclo en {intervalo // 60} min...")
        time.sleep(intervalo)

    print("[monitor] Vigilancia finalizada.")


if __name__ == "__main__":
    vigilar()
