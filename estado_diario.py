"""
estado_diario.py
-----------------
SIN CAMBIOS por la migracion a ESPN. GitHub Actions no garantiza que un
workflow programado a una hora exacta corra justo a esa hora -- puede
atrasarse o saltarse el dia. Por eso las fases de "un solo disparo"
(resumen 7am, cierre 23:30, reporte 6am) reintentan cada 15 min dentro
de una ventana de 1-2 horas. Este modulo lleva el control de "ya se
hizo hoy" para cada una.
"""

import json
import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_ESTADO = DATA_DIR / "estado_diario.json"
ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))


def _fecha_local_hoy():
    return datetime.datetime.now(ZONA_HORARIA_LOCAL).date().isoformat()


def _cargar():
    hoy = _fecha_local_hoy()
    if ARCHIVO_ESTADO.exists():
        try:
            estado = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))
            if estado.get("fecha") == hoy:
                return estado
        except Exception:
            pass
    return {"fecha": hoy}


def ya_se_hizo(tarea):
    estado = _cargar()
    return estado.get(tarea, False) is True


def marcar_hecho(tarea):
    DATA_DIR.mkdir(exist_ok=True)
    estado = _cargar()
    estado[tarea] = True
    ARCHIVO_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
