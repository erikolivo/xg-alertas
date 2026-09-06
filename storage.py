"""
storage.py
----------
SIN CAMBIOS. Unico punto de acceso para lectura y escritura de datos
JSON. Disponible como utilidad, sin forzar su uso en el resto de
modulos (ver CONSOLIDACION.md).
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


class Storage:
    @staticmethod
    def leer_json(ruta_relativa, default_val=None):
        archivo = DATA_DIR / ruta_relativa
        if archivo.exists():
            try:
                return json.loads(archivo.read_text(encoding="utf-8"))
            except Exception:
                pass
        return default_val if default_val is not None else {}

    @staticmethod
    def guardar_json(ruta_relativa, datos):
        archivo = DATA_DIR / ruta_relativa
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
