"""
xg_engine.py
------------
Motor de alertas basado exclusivamente en xG (Expected Goals).

Reemplaza a momentum.py del proyecto original. No usa tiros, corners,
posesion ni z-scores -- solo la diferencia de xG entre equipos para
determinar dominancia.

Tipos de alerta:
  1. 🟠 Dominancia xG      — xG diff >= UMBRAL, va empatado o perdiendo
  2. 🔴 No favorito domina  — el no-favorito tiene mayor xG
  3. 🎯 xG vs Marcador     — domina en xG pero va perdiendo/empatando
  4. ⏰ Cierre              — min 75+, mismo equipo dominando
  5. 🔵 Ampliación          — favorito ganando y sigue dominando en xG
"""


# --- Umbrales (configurables) ---
UMBRAL_XG_DOMINANCIA = 0.8     # xG diff minimo para alertar dominancia
UMBRAL_XG_FUERTE = 1.2         # dominancia fuerte
UMBRAL_XG_CIERRE = 0.8         # para alertas de cierre (min 75+)
MINUTOS_MINIMOS_XG = 15        # minutos minimos para que xG sea significativo
VENTANA_DEDUPLICACION = 15     # minutos para no repetir alerta del mismo tipo


def _minuto_a_entero(minuto):
    """Convierte '45'+4' o '52' o '45+2' a entero."""
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
                     minuto, favorito_es_local, historial_alertas,
                     minuto_inicio=None):
    """
    Evalua que tipo de alerta corresponde segun el estado actual del xG.

    Devuelve dict con:
      - tipo: str (None si no hay alerta)
      - emoji: str
      - mensaje: str
      - xg_diff: float
    """
    minuto_int = _minuto_a_entero(minuto)
    if minuto_int is None or minuto_int < MINUTOS_MINIMOS_XG:
        return None

    xg_diff = calcular_diferencia_xg(xg_home, xg_away)
    xg_diff_abs = abs(xg_diff)

    # Determinar que equipo domina
    if xg_diff > 0:
        equipo_dominante = "home"
        xg_dominante = xg_home
        xg_rival = xg_away
    elif xg_diff < 0:
        equipo_dominante = "away"
        xg_dominante = xg_away
        xg_rival = xg_home
    else:
        return None

    # Verificar si el dominante es el favorito
    dominante_es_favorito = (equipo_dominante == "home" and favorito_es_local) or \
                            (equipo_dominante == "away" and not favorito_es_local)

    # Verificar si el dominante va ganando
    if equipo_dominante == "home":
        dominante_va_ganando = goles_home > goles_away
        dominante_va_perdiendo = goles_home < goles_away
        dominante_va_empatando = goles_home == goles_away
    else:
        dominante_va_ganando = goles_away > goles_home
        dominante_va_perdiendo = goles_away < goles_home
        dominante_va_empatando = goles_away == goles_home

    # --- ALERTA 4: Cierre (min 75+) ---
    if minuto_int >= 75 and xg_diff_abs >= UMBRAL_XG_CIERRE:
        if dominante_va_ganando or dominante_va_empatando:
            return _construir_alerta(
                tipo="cierre",
                emoji="⏰",
                texto="Posible gol de cierre",
                xg_home=xg_home, xg_away=xg_away,
                goles_home=goles_home, goles_away=goles_away,
                minuto=minuto, xg_diff=xg_diff
            )

    # --- ALERTA 5: Ampliación ---
    if dominante_va_ganando and dominante_es_favorito and xg_diff_abs >= UMBRAL_XG_DOMINANCIA:
        return _construir_alerta(
            tipo="ampliacion",
            emoji="🔵",
            texto="Posible ampliación de marcador",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff
        )

    # --- ALERTA 3: xG vs Marcador ---
    if (dominante_va_perdiendo or dominante_va_empatando) and xg_diff_abs >= UMBRAL_XG_DOMINANCIA:
        return _construir_alerta(
            tipo="xg_vs_marcador",
            emoji="🎯",
            texto="Dominio en xG pero va perdiendo/empatando",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff
        )

    # --- ALERTA 1: Dominancia xG ---
    if xg_diff_abs >= UMBRAL_XG_DOMINANCIA and (dominante_va_empatando or dominante_va_perdiendo):
        return _construir_alerta(
            tipo="dominancia_xg",
            emoji="🟠",
            texto="Dominancia xG",
            xg_home=xg_home, xg_away=xg_away,
            goles_home=goles_home, goles_away=goles_away,
            minuto=minuto, xg_diff=xg_diff
        )

    # --- ALERTA 2: No favorito domina ---
    if not dominante_es_favorito and xg_diff_abs >= UMBRAL_XG_DOMINANCIA:
        if dominante_va_empatando or dominante_va_perdiendo:
            return _construir_alerta(
                tipo="no_fav_domina",
                emoji="🔴",
                texto="Posible gol del no favorito",
                xg_home=xg_home, xg_away=xg_away,
                goles_home=goles_home, goles_away=goles_away,
                minuto=minuto, xg_diff=xg_diff
            )

    return None


def _construir_alerta(tipo, emoji, texto, xg_home, xg_away,
                       goles_home, goles_away, minuto, xg_diff):
    """Construye el dict de alerta con el mensaje formateado."""
    diff_display = f"+{xg_diff:.2f}" if xg_diff > 0 else f"{xg_diff:.2f}"

    mensaje = (
        f"Alertas XG\n"
        f"{emoji} {texto} — min {minuto}'\n"
        f"\n"
        f"<b>▸ xG: {xg_home:.2f} vs {xg_away:.2f} (diff: {diff_display})</b>\n"
        f"Marcador real: {goles_home} - {goles_away}"
    )

    return {
        "tipo": tipo,
        "emoji": emoji,
        "mensaje": mensaje,
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
