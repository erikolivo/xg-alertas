"""
xg_engine.py
------------
Motor de alertas basado exclusivamente en xG (Expected Goals).

No usa posesion, corners ni z-scores: solo la diferencia de xG, el marcador,
el favorito real del partido y el contexto (expulsiones, volumen de tiros).

Las reglas fueron calibradas con un backtest de 199 partidos terminados de
16 ligas ESPN (2026-10-25 a 2026-11-07). Resultado: ~70% de acierto frente a
~50% de la version anterior (que alertaba en cualquier empate, con el equipo
perdiendo o despues del minuto 70).

Tipos de alerta (los 5 siguen existiendo):
  1. 🟠 Dominancia xG      — el FAVORITO domina un 0-0 (min 20-49)
  2. ⏰ Cierre              — el FAVORITO domina un 0-0 en el tramo final (50-60)
  3. 🔵 Ampliación          — el FAVORITO gana y sigue dominando en xG (20-55)
  4. 🎯 xG vs Marcador     — el FAVORITO pierde pero domina de forma extrema (20-55)
  5. 🔴 No favorito domina  — DESACTIVADO: el underdog dominando no tiene edge

Evidencia del backtest (ACIERTO = el dominante marca antes del final):
  - Favorito domina 0-0 .......... 86%  (antes: 63%)
  - Favorito gana y domina ....... 74%  (antes: 60%)
  - Favorito domina 0-0 (50-60) .. 71%  (antes: 43%)
  - Underdog domina .............. 46%  -> desactivado
  - Equipo perdiendo dominando ... 40%  -> solo si el favorito pierde y diff >= 1.5
  - Alertas despues del min 70 ... 42%  -> eliminadas
"""


# --- Umbrales (calibrados con backtest, NO tocar sin volver a medir) ---
UMBRAL_XG_DOMINANCIA = 0.80    # favorito empatando 0-0
UMBRAL_XG_AMPLIACION = 0.70    # favorito ganando y siguiendo arriba
UMBRAL_XG_CIERRE = 0.80        # favorito 0-0 en el tramo final
UMBRAL_XG_REMARCADA = 1.50     # favorito perdiendo pero dominando de forma extrema
UMBRAL_XG_FUERTE = 0.90        # dominancia fuerte (se salta filtros de confirmacion)
UMBRAL_XG_RECIENTE = 0.05      # xG diff minimo en los ultimos 15 min
MINUTOS_MINIMOS_XG = 20        # antes del min 20 el xG es poco significativo
VENTANA_DEDUPLICACION = 15     # minutos para no repetir alerta del mismo tipo

# Limite unico: ninguna alerta despues del minuto 70
LIMITE_ALERTAS = 70

MINUTOS_CIERRE = 50            # ventana de cierre: 50-60
MINUTOS_CIERRE_FIN = 60
MINIMO_TIROS_PUERTA = 2        # volumen minimo del dominante para alertar
MINIMO_GRANDES_OCASIONES = 1   # al menos una ocasion clara (xG >= 0.30)

# El underdog dominando no mostro edge en el backtest (46%). Desactivado por
# defecto; se puede reactivar con la variable de entorno PERMITIR_NO_FAVORITO=1.
import os as _os

PERMITIR_NO_FAVORITO = _os.getenv("PERMITIR_NO_FAVORITO", "0").strip() == "1"


def _minuto_a_entero(minuto):
    """Convierte '45'+4'' o '52' o '45+2' a entero."""
    if minuto is None:
        return None
    try:
        import re
        limpio = re.sub(r"[^0-9+]", "", str(minuto))
        partes = limpio.split("+")
        return int(partes[0]) if partes[0] else None
    except (TypeError, ValueError):
        return None


def calcular_diferencia_xg(xg_home, xg_away):
    """Devuelve la diferencia de xG (positivo = home dominando)."""
    try:
        h = float(xg_home or 0)
        a = float(xg_away or 0)
        return round(h - a, 4)
    except (TypeError, ValueError):
        return 0.0


def evaluar_alertas(xg_home, xg_away, goles_home, goles_away,
                    minuto, favorito_es_local, historial_alertas=None,
                    minuto_inicio=None, xg_reciente=None, contexto=None):
    """
    Evalua que tipo de alerta corresponde segun el estado actual del partido.

    Parametros:
      xg_home, xg_away      : xG actual de cada equipo
      goles_home, goles_away: marcador real
      minuto                : reloj de ESPN ("67'")
      favorito_es_local     : True / False / None (desconocido)
      xg_reciente           : {"home": x, "away": y} de los ultimos 15 min
      contexto              : {"rojas_home", "rojas_away",
                               "tiros_puerta_home", "tiros_puerta_away",
                               "tiros_totales_home", "tiros_totales_away"}

    Devuelve dict o None.
    """
    minuto_int = _minuto_a_entero(minuto)
    if minuto_int is None or minuto_int < MINUTOS_MINIMOS_XG:
        return None

    # Limite unico: ninguna alerta despues del minuto 70
    if minuto_int > LIMITE_ALERTAS:
        return None

    xg_diff = calcular_diferencia_xg(xg_home, xg_away)
    xg_diff_abs = abs(xg_diff)
    # Umbral mas bajo de todos: si no llega ni a ampliacion, no hay nada
    if xg_diff_abs < UMBRAL_XG_AMPLIACION:
        return None

    # Determinar que equipo domina
    if xg_diff > 0:
        dom, otro = "home", "away"
    else:
        dom, otro = "away", "home"

    ctx = contexto or {}

    # --- 1. El dominante no puede jugar con uno menos ---
    rojas_dom = ctx.get("rojas_" + dom, 0) or 0
    rojas_riv = ctx.get("rojas_" + otro, 0) or 0
    if rojas_dom > rojas_riv:
        return None

    # --- 2. Volumen minimo de tiros a puerta ---
    tp_dom = ctx.get("tiros_puerta_" + dom)
    tt_dom = ctx.get("tiros_totales_" + dom)
    if tp_dom is not None and tt_dom and tp_dom < MINIMO_TIROS_PUERTA:
        return None

    # --- 3. Dominio reciente (evita alertas "zombi") ---
    # Si la diferencia acumulada ya es >= 1.00, no hace falta el filtro.
    if xg_diff_abs < 1.00 and xg_reciente:
        rec_dom = float(xg_reciente.get(dom) or 0)
        rec_riv = float(xg_reciente.get(otro) or 0)
        if (rec_dom - rec_riv) < UMBRAL_XG_RECIENTE:
            return None

    # --- 4. Ocasiones claras (solo si el play-by-play las trae) ---
    grandes = None
    if ctx.get("grandes_home") is not None or ctx.get("grandes_away") is not None:
        grandes = {
            "home": ctx.get("grandes_home") or 0,
            "away": ctx.get("grandes_away") or 0,
        }
    if grandes is not None and xg_diff_abs < 1.00:
        if (grandes.get(dom) or 0) < MINIMO_GRANDES_OCASIONES:
            return None

    # --- 5. Estado del marcador ---
    if dom == "home":
        va_ganando = goles_home > goles_away
        va_perdiendo = goles_home < goles_away
        va_empatando = goles_home == goles_away
    else:
        va_ganando = goles_away > goles_home
        va_perdiendo = goles_away < goles_home
        va_empatando = goles_away == goles_home

    cero_cero = (goles_home == 0 and goles_away == 0)

    # --- 6. Favorito real ---
    if favorito_es_local is None:
        # Sin cuotas no se distingue favorito de underdog: mejor no alertar.
        return None
    dom_es_favorito = (dom == "home" and bool(favorito_es_local)) or \
                      (dom == "away" and not bool(favorito_es_local))

    extras = {
        "xg_reciente": xg_reciente,
        "grandes": grandes,
    }

    # --- ⏰ CIERRE: favorito domina un 0-0 en el tramo final (50-60) ---
    if (cero_cero and dom_es_favorito
            and MINUTOS_CIERRE <= minuto_int <= MINUTOS_CIERRE_FIN
            and xg_diff_abs >= UMBRAL_XG_CIERRE):
        return _construir_alerta(
            tipo="cierre",
            emoji="⏰",
            texto="Última ventana de gol",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- 🟠 DOMINANCIA: favorito domina un 0-0 (20-49) ---
    if (cero_cero and dom_es_favorito
            and minuto_int < MINUTOS_CIERRE
            and xg_diff_abs >= UMBRAL_XG_DOMINANCIA):
        return _construir_alerta(
            tipo="dominancia_xg",
            emoji="🟠",
            texto="El favorito domina 0-0",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- 🔵 AMPLIACION: favorito gana y sigue dominando (20-55) ---
    if (va_ganando and dom_es_favorito and minuto_int <= 55
            and xg_diff_abs >= UMBRAL_XG_AMPLIACION):
        return _construir_alerta(
            tipo="ampliacion",
            emoji="🔵",
            texto="El favorito sigue atacando",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- 🎯 XG VS MARCADOR: favorito pierde pero domina de forma extrema ---
    if (va_perdiendo and dom_es_favorito and minuto_int <= 55
            and xg_diff_abs >= UMBRAL_XG_REMARCADA):
        return _construir_alerta(
            tipo="xg_vs_marcador",
            emoji="🎯",
            texto="Remontada en marcha",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- 🔴 NO FAVORITO: desactivado por defecto (sin edge en el backtest) ---
    if (PERMITIR_NO_FAVORITO and not dom_es_favorito
            and cero_cero and minuto_int <= 55
            and xg_diff_abs >= 1.00):
        return _construir_alerta(
            tipo="no_fav_domina",
            emoji="🔴",
            texto="Posible gol del no favorito",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    return None


def _construir_alerta(tipo, emoji, texto, xg_home, xg_away,
                      goles_home, goles_away, minuto, xg_diff, extras=None):
    """Construye el dict de alerta con el mensaje formateado."""
    extras = extras or {}
    diff_display = f"+{xg_diff:.2f}" if xg_diff > 0 else f"{xg_diff:.2f}"

    lineas = [
        "Alertas XG",
        f"{emoji} {texto} — min {minuto}",
        "",
        f"<b>▸ xG: {xg_home:.2f} vs {xg_away:.2f} (diff: {diff_display})</b>",
    ]

    reciente = extras.get("xg_reciente") or {}
    rh = float(reciente.get("home") or 0)
    ra = float(reciente.get("away") or 0)
    if rh > 0 or ra > 0:
        lineas.append(f"▸ Últimos 15': {rh:.2f} vs {ra:.2f}")

    grandes = extras.get("grandes") or {}
    gh = grandes.get("home", 0)
    ga = grandes.get("away", 0)
    if gh or ga:
        lineas.append(f"▸ Grandes ocasiones: {gh} vs {ga}")

    return {
        "tipo": tipo,
        "emoji": emoji,
        "mensaje": "\n".join(lineas),
        "xg_diff": xg_diff,
        "xg_home": xg_home,
        "xg_away": xg_away,
    }


def calcular_zona_xg(xg_diff):
    """Clasifica la diferencia de xG en una zona."""
    diff = abs(xg_diff)
    if diff >= UMBRAL_XG_FUERTE:
        return "dominancia_fuerte"
    elif diff >= UMBRAL_XG_DOMINANCIA:
        return "dominancia"
    elif diff >= 0.3:
        return "leve_ventaja"
    else:
        return "parejo"


def descripcion_xg(xg_home, xg_away, goles_home, goles_away):
    """Descripcion corta del estado de xG para el resumen."""
    diff = calcular_diferencia_xg(xg_home, xg_away)
    zona = calcular_zona_xg(diff)

    luck = ""
    if goles_home is not None and goles_away is not None:
        if diff > 0.5 and goles_home <= goles_away:
            luck = " (suerte favorable al visitante)"
        elif diff < -0.5 and goles_away <= goles_home:
            luck = " (suerte favorable al local)"

    mapa = {
        "dominancia_fuerte": "Dominancia clara",
        "dominancia": "Un equipo domina",
        "leve_ventaja": "Leve ventaja",
        "parejo": "Partido parejo",
    }
    return f"{mapa[zona]}{luck}"
