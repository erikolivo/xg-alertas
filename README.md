# fotmob-xg-alertas

Alertas de dominancia por xG en vivo desde FotMob. Sistema automatizado que monitorea partidos seleccionados y avisa por Telegram cuando un equipo domina en Expected Goals.

## Las 5 fases

| Fase | Cuándo | Qué hace |
|------|--------|----------|
| 1. Selección | 04:00 | Lee favoritos de Google Sheets, busca fixtures en FotMob |
| 2. Resumen | 07:00 | Envía lista de partidos del día por Telegram |
| 3. Vigilancia | 09:00-20:00 | Cada 5-15 min: obtiene xG + stats, evalúa alertas |
| 4. Cierre | 23:30 | Obtiene resultados, audita alertas, archiva día |
| Reporte | 06:00 (día+1) | Estadísticas de acierto por tipo de alerta |

## Tipos de alerta

| Emoji | Tipo | Cuándo |
|-------|------|--------|
| 🟠 | Dominancia xG | xG diff ≥ 0.8, va empatado o perdiendo |
| 🔴 | No favorito domina | El no-favorito tiene mayor xG |
| 🎯 | xG vs Marcador | Domina en xG pero va perdiendo/empatando |
| ⏰ | Cierre | Min 75+, mismo equipo dominando |
| 🔵 | Ampliación | Favorito ganando y sigue dominando en xG |

## Datos de cada alerta

Cada alerta incluye:
- xG de ambos equipos
- Diferencia de xG
- Marcador real
- Estadísticas completas (posesión, tiros, corners, faltas)

## Cómo ponerlo en línea

### 1. Crear repositorio
```bash
cd fotmob-xg-alertas
git init
git add .
git commit -m "v1: alertas xG desde FotMob"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/fotmob-xg-alertas.git
git push -u origin main
```

### 2. Permisos de escritura
`Settings → Actions → Workflow permissions → Read and write permissions`

### 3. Secrets
`Settings → Secrets and variables → Actions`

| Nombre | Valor |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | Token de tu bot (@BotFather) |
| `TELEGRAM_CHAT_ID` | Tu chat ID |

### 4. Probar
`Actions → Fase 1 - Seleccion → Run workflow`

## Estructura

```
fotmob_client.py        -> conexión HTTP a FotMob (sesión, buildId)
xg_engine.py            -> motor de alertas basado en xG
fetch_data.py           -> obtención de fixtures, xG, stats, eventos
seleccionar_partidos.py -> Fase 1: favoritos + fixtures
resumen.py              -> Fase 2: resumen pre-partido
monitor.py              -> Fase 3: vigilancia en vivo + alertas
cerrar_resultados.py    -> Fase 4: resultados + auditoría + Excel
reporte_diario.py       -> Reporte de las 6am
google_favoritos.py     -> lectura de Google Sheets
telegram_utils.py       -> envío de mensajes Telegram
estado_diario.py        -> control de "ya se hizo hoy"
storage.py              -> utilidad JSON
```

## Configuración

Umbrales en `xg_engine.py`:
- `UMBRAL_XG_DOMINANCIA = 0.8` — xG diff mínimo para alertar
- `UMBRAL_XG_FUERTE = 1.2` — dominancia fuerte
- `MINUTOS_MINIMOS_XG = 15` — mínimo de minutos para que xG sea significativo
- `VENTANA_DEDUPLICACION = 15` — minutos para no repetir alerta

Ligas en `fetch_data.py` → `LIGAS_FOTMOB`: agregar el ID de FotMob.

## Requisitos

- Python 3.12+
- `requests>=2.31.0`
- `openpyxl>=3.1.0`
