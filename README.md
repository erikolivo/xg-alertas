# xg-alertas

Alertas de dominancia por xG en vivo desde **ESPN**. Sistema automatizado que
monitorea partidos de 16 ligas y avisa por Telegram cuando un equipo domina en
Expected Goals.

## Las 5 fases

| Fase | Cuándo | Qué hace |
|------|--------|----------|
| 1. Selección | 04:00 | Busca fixtures del día en ESPN |
| 2. Resumen | 07:00 | Envía lista de partidos del día por Telegram |
| 3. Vigilancia | 09:00-20:00 | Cada 5-15 min: obtiene xG + stats, evalúa alertas |
| 4. Cierre | 23:30 | Obtiene resultados, audita alertas, archiva día |
| Reporte | 06:00 (día+1) | Estadísticas de acierto por tipo de alerta |

## Tipos de alerta

| Emoji | Tipo | Cuándo |
|-------|------|--------|
| 🟠 | Dominancia xG | xG diff ≥ 0.50 y va empatando |
| 🔴 | No favorito domina | El no-favorito domina en xG y no gana |
| 🎯 | xG vs Marcador | Domina en xG pero va perdiendo |
| ⏰ | Cierre | Min 75-80, sigue dominando y va empatando/ganando |
| 🔵 | Ampliación | El favorito gana y sigue dominando en xG |

**Ninguna alerta se envía después del minuto 80.**

## Cómo se calcula el xG

ESPN no publica xG, pero sí las coordenadas de cada tiro en el play-by-play.
El sistema aplica un modelo logístico de distancia y ángulo al arco:

```
xG = sigmoid(-1.85 - 0.08·distancia_m + 2.40·ángulo_rad)
```

- Cabezazos: ×0.65
- Penales: 0.78
- Si el play-by-play está incompleto (<60% de los tiros del boxscore) se usa
  una estimación por volumen (SOT/off-target/bloqueados) para ambos equipos.

## Filtros anti-falsos positivos

- **Favorito real** desde las cuotas de ESPN (`pickcenter`). Si no hay cuotas,
  no se emiten 🔴 ni 🔵 (antes se asumía local favorito y se etiquetaba mal).
- **Dominio reciente**: el diff de los últimos 15 min debe ser ≥ 0.08, salvo
  que el diff acumulado sea ≥ 0.75 (dominancia fuerte).
- **Confirmación**: una señal marginal debe repetirse en dos lecturas seguidas.
- **Contexto**: se descarta la alerta si el dominante juega con un expulsado
  o no acumula al menos 2 tiros a puerta.
- **Anti-repetición**: mínimo 6 min entre alertas del mismo partido y 15 min
  para el mismo tipo, salvo que el xG crezca ≥ 0.30.

## Seguimiento de acierto

Cada alerta guarda el equipo dominante y el marcador al momento del envío.
Después:

- ✅ **ACIERTO** si el dominante marca.
- ❌ **FALLO** si marca el rival o el partido termina sin gol del dominante.

## Estructura

```
xg_engine.py            -> motor de alertas basado en xG
fetch_data.py           -> fixtures, xG por coordenadas, stats, cuotas
seleccionar_partidos.py -> Fase 1: fixtures del día
resumen.py              -> Fase 2: resumen pre-partido
monitor.py              -> Fase 3: vigilancia en vivo + alertas + ACIERTO/FALLO
cerrar_resultados.py    -> Fase 4: resultados + auditoría
reporte_diario.py       -> Reporte de las 6am
telegram_utils.py       -> envío de mensajes Telegram
estado_diario.py        -> control de "ya se hizo hoy"
```

## Configuración

Umbrales en `xg_engine.py`:

- `UMBRAL_XG_DOMINANCIA = 0.50` — xG diff mínimo para alertar
- `UMBRAL_XG_FUERTE = 0.75` — dominancia fuerte (se salta filtros)
- `UMBRAL_XG_RECIENTE = 0.08` — xG diff mínimo en los últimos 15 min
- `LIMITE_ALERTAS = 80` — minuto máximo para cualquier alerta
- `VENTANA_DEDUPLICACION = 15` — minutos para no repetir el mismo tipo

Ligas en `fetch_data.py` → `LIGAS_ESPN`.

## Requisitos

- Python 3.12+
- `requests>=2.31.0`
- `openpyxl>=3.1.0`

## Secrets de GitHub Actions

| Nombre | Valor |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | Token de tu bot (@BotFather) |
| `TELEGRAM_CHAT_ID` | Tu chat ID |
