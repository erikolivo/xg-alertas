# xg-alertas

Alertas de dominancia por xG en vivo desde **ESPN**. Sistema automatizado que
monitorea partidos de 16 ligas y avisa por Telegram cuando el **favorito** está
a punto de marcar.

Las reglas están calibradas con un backtest de **199 partidos terminados**
(`python backtest.py`). Tasa de acierto actual: **78%** (antes: 56%).

## Las 5 fases

| Fase | Cuándo | Qué hace |
|------|--------|----------|
| 1. Selección | 04:00 | Busca fixtures del día en ESPN |
| 2. Resumen | 07:00 | Envía lista de partidos del día por Telegram |
| 3. Vigilancia | 09:00-20:00 | Cada 5-15 min: obtiene xG + stats, evalúa alertas |
| 4. Cierre | 23:30 | Obtiene resultados, audita alertas, archiva día |
| Reporte | 06:00 (día+1) | Estadísticas de acierto por tipo de alerta |

## Tipos de alerta

| Emoji | Tipo | Cuándo | Acierto medido |
|-------|------|--------|----------------|
| 🟠 | Dominancia xG | El **favorito** domina un **0-0**, min 20-49, diff ≥ 0.80 | 88% |
| ⏰ | Cierre | El **favorito** domina un **0-0**, min 50-60, diff ≥ 0.80 | 67% |
| 🔵 | Ampliación | El **favorito gana** y sigue dominando, min 20-55, diff ≥ 0.70 | 77% |
| 🎯 | xG vs Marcador | El **favorito pierde** pero domina de forma extrema (diff ≥ 1.50) | 100%* |
| 🔴 | No favorito domina | El underdog domina | **desactivado** |

\* Solo 2 casos en el backtest: se mantiene por ser una señal extrema, pero es
rara.

**Ninguna alerta se envía después del minuto 70.**

### Por qué el 🔴 está desactivado

En el backtest, cuando el *underdog* domina en xG, marca después solo el **46%**
de las veces (peor que una moneda). El mercado ya lo considera peor equipo y la
"dominancia" suele ser ruido de muestra pequeña. Se puede reactivar con la
variable de entorno `PERMITIR_NO_FAVORITO=1`, pero no se recomienda.

### Por qué solo 0-0 y no 1-1

Con 0-0 el equipo dominante marca después el **76%** de las veces. Con 1-1 baja
al **57%** (y al 37% en el conjunto de prueba): en un partido ya abierto, ambos
equipos han demostrado que pueden marcar y la ventaja de xG se diluye.

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

- **Solo el favorito**: las alertas se emiten cuando domina el equipo favorito
  según las cuotas de ESPN (`pickcenter`). Si no hay cuotas, no se alerta.
- **Solo 0-0** para dominancia y cierre.
- **Dominio reciente**: el diff de los últimos 15 min debe ser ≥ 0.05, salvo
  que el diff acumulado sea ≥ 1.00.
- **Ocasiones claras**: al menos 1 ocasión con xG ≥ 0.30, salvo diff ≥ 1.00.
- **Contexto**: se descarta si el dominante juega con un expulsado o no acumula
  al menos 2 tiros a puerta.
- **Confirmación**: una señal marginal debe repetirse en dos lecturas seguidas.
- **Anti-repetición**: mínimo 6 min entre alertas del mismo partido y 15 min
  para el mismo tipo, salvo que el xG crezca ≥ 0.30.

## Seguimiento de acierto

Cada alerta guarda el equipo dominante y el marcador al momento del envío.
Después:

- ✅ **ACIERTO** si el dominante marca.
- ❌ **FALLO** si marca el rival primero o el partido termina sin gol del
  dominante.

Todo se guarda en `data/alertas_log.jsonl` (append-only), que **sobrevive a los
reinicios de la Fase 1**. Para ver el acumulado:

```bash
python alertas_log.py
```

## Backtest

```bash
python backtest.py               # mide el motor con la cache local
python backtest.py --recolectar  # vuelve a descargar partidos de ESPN
```

Resultado con 199 partidos (validación train/test):

```
TRAIN    56 alertas (0.57/p)  ACIERTO 80.4%
TEST     68 alertas (0.68/p)  ACIERTO 76.5%
TODO    124 alertas (0.62/p)  ACIERTO 78.2%
```

La versión anterior hacía 517 alertas (2.60/partido) con 55.9% de acierto.

## Estructura

```
xg_engine.py            -> motor de alertas basado en xG
fetch_data.py           -> fixtures, xG por coordenadas, stats, cuotas
seleccionar_partidos.py -> Fase 1: fixtures del día (preserva historial)
resumen.py              -> Fase 2: resumen pre-partido
monitor.py              -> Fase 3: vigilancia en vivo + alertas + ACIERTO/FALLO
cerrar_resultados.py    -> Fase 4: resultados + auditoría
reporte_diario.py       -> Reporte de las 6am
alertas_log.py          -> log persistente de alertas y resoluciones
backtest.py             -> medición de acierto sobre partidos terminados
telegram_utils.py       -> envío de mensajes Telegram
estado_diario.py        -> control de "ya se hizo hoy"
```

## Configuración

Umbrales en `xg_engine.py`:

- `UMBRAL_XG_DOMINANCIA = 0.80` — favorito empatando 0-0
- `UMBRAL_XG_AMPLIACION = 0.70` — favorito ganando
- `UMBRAL_XG_CIERRE = 0.80` — favorito 0-0 en tramo final
- `UMBRAL_XG_REMARCADA = 1.50` — favorito perdiendo pero dominando mucho
- `UMBRAL_XG_FUERTE = 0.90` — dominancia fuerte (se salta confirmación)
- `UMBRAL_XG_RECIENTE = 0.05` — xG diff mínimo en los últimos 15 min
- `LIMITE_ALERTAS = 70` — minuto máximo para cualquier alerta
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
