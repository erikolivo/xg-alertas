"""
xg_engine.py
------------
Motor de alertas basado exclusivamente en xG (Expected Goals).

No usa posesion, corners ni z-scores: solo la diferencia de xG, el marcador,
el favorito real del partido y el contexto (expulsiones, volumen de tiros).

Tipos de alerta:
  1. 🟠 Dominancia xG      — domina en xG y va empatando
  2. 🔴 No favorito domina  — el no-favorito domina en xG y no gana
  3. 🎯 xG vs Marcador     — domina en xG pero va perdiendo
  4. ⏰ Cierre              — min 75+, sigue dominando y va empatando/ganando
  5. 🔵 Ampliación          — el favorito gana y sigue dominando en xG
"""


# --- Umbrales (configurables) ---
UMBRAL_XG_DOMINANCIA = 0.50    # xG diff minimo para alertar dominancia
UMBRAL_XG_FUERTE = 0.75        # dominancia fuerte (se salta filtros de confirmacion)
UMBRAL_XG_CIERRE = 0.50        # para alertas de cierre (min 75+)
UMBRAL_XG_RECIENTE = 0.08      # xG diff minimo en los ultimos 15 min
MINUTOS_MINIMOS_XG = 15        # minutos minimos para que xG sea significativo
VENTANA_DEDUPLICACION = 15     # minutos para no repetir alerta del mismo tipo

# Limite unico: TODAS las alertas hasta minuto 80
LIMITE_ALERTAS = 80            # mas alla del min 80 no se envia nada

MINUTOS_CIERRE = 75            # desde este minuto aplica la alerta de cierre
MINIMO_TIROS_PUERTA = 2        # volumen minimo del dominante para alertar


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

    # Limite unico: ninguna alerta mas alla del minuto 80
    if minuto_int > LIMITE_ALERTAS:
        return None

    xg_diff = calcular_diferencia_xg(xg_home, xg_away)
    xg_diff_abs = abs(xg_diff)
    if xg_diff_abs < UMBRAL_XG_DOMINANCIA:
        return None

    # Determinar que equipo domina
    if xg_diff > 0:
        dom, otro = "home", "away"
    else:
        dom, otro = "away", "home"

    # --- Filtros de contexto ---
    ctx = contexto or {}

    rojas_dom = ctx.get("rojas_" + dom, 0) or 0
    rojas_riv = ctx.get("rojas_" + otro, 0) or 0
    if rojas_dom > rojas_riv:
        # El dominante juega con uno menos: situacion poco fiable
        return None

    tp_dom = ctx.get("tiros_puerta_" + dom)
    tt_dom = ctx.get("tiros_totales_" + dom)
    if tp_dom is not None and tt_dom and tp_dom < MINIMO_TIROS_PUERTA:
        return None

    # --- Filtro de dominio reciente ---
    # Evita alertas "zombi": equipos que dominaron hace 30 min y ya no generan.
    if xg_diff_abs < UMBRAL_XG_FUERTE and xg_reciente:
        rec_dom = float(xg_reciente.get(dom) or 0)
        rec_riv = float(xg_reciente.get(otro) or 0)
        if (rec_dom - rec_riv) < UMBRAL_XG_RECIENTE:
            return None

    # --- Estado del marcador ---
    if dom == "home":
        va_ganando = goles_home > goles_away
        va_perdiendo = goles_home < goles_away
        va_empatando = goles_home == goles_away
    else:
        va_ganando = goles_away > goles_home
        va_perdiendo = goles_away < goles_home
        va_empatando = goles_away == goles_home

    # --- Favorito real ---
    if favorito_es_local is None:
        dom_es_favorito = None
    else:
        dom_es_favorito = (dom == "home" and bool(favorito_es_local)) or \
                          (dom == "away" and not bool(favorito_es_local))

    grandes = None
    if ctx.get("grandes_home") is not None or ctx.get("grandes_away") is not None:
        grandes = {
            "home": ctx.get("grandes_home") or 0,
            "away": ctx.get("grandes_away") or 0,
        }

    extras = {
        "xg_reciente": xg_reciente,
        "grandes": grandes,
    }

    # --- ALERTA 4: Cierre (min 75-80) ---
    if minuto_int >= MINUTOS_CIERRE and xg_diff_abs >= UMBRAL_XG_CIERRE:
        if va_ganando or va_empatando:
            return _construir_alerta(
                tipo="cierre",
                emoji="⏰",
                texto="Posible gol de cierre",
                xg_home=xg_home, xg_away=xg_away,
                goles_home=goles_home, goles_away=goles_away,
                minuto=minuto, xg_diff=xg_diff, extras=extras
            )

    # --- ALERTA 5: Ampliación (favorito ganando y dominando) ---
    if va_ganando and dom_es_favorito is True and xg_diff_abs >= UMBRAL_XG_DOMINANCIA:
        return _construir_alerta(
            tipo="ampliacion",
            emoji="🔵",
            texto="Posible ampliación de marcador",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- ALERTA 2: No favorito domina (underdog dominando y sin ganar) ---
    if dom_es_favorito is False and (va_empatando or va_perdiendo):
        return _construir_alerta(
            tipo="no_fav_domina",
            emoji="🔴",
            texto="Posible gol del no favorito",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- ALERTA 3: xG vs Marcador (domina pero va perdiendo) ---
    if va_perdiendo:
        return _construir_alerta(
            tipo="xg_vs_marcador",
            emoji="🎯",
            texto="Dominio en xG pero va perdiendo",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff, extras=extras
        )

    # --- ALERTA 1: Dominancia xG (domina y va empatando) ---
    if va_empatando:
        return _construir_alerta(
            tipo="dominancia_xg",
            emoji="🟠",
            texto="Dominancia xG",
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
