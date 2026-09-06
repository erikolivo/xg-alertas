import sys, os
sys.path.insert(0, "C:/Users/HP/Documents/Default Project/fotmob-xg-alertas")
os.environ["TELEGRAM_BOT_TOKEN"] = "8997939346:AAF0QeKwqPDl58tKp7Ppsn2p8awqF0iopP4"
os.environ["TELEGRAM_CHAT_ID"] = "5219327292"

import json
from pathlib import Path

data_dir = Path("C:/Users/HP/Documents/Default Project/fotmob-xg-alertas/data")
archivo = data_dir / "partidos_hoy.json"
data = json.loads(archivo.read_text(encoding="utf-8"))
partidos = data.get("partidos", [])

print(f"Fecha: {data.get('fecha')}")
print(f"Total partidos: {len(partidos)}")

# Probar 3 partidos con slug conocido
test = [p for p in partidos if p.get("liga_slug") and p.get("liga_slug") != "all"][:3]
for p in test:
    nombre = p["local"] + " vs " + p["visitante"]
    slug = p.get("liga_slug", "?")
    print(f"\n--- {nombre} ({slug}) ---")
    from monitor import _procesar_partido
    _procesar_partido(p)
    res = p.get("resultado_final")
    if res:
        print(f"  TERMINADO: {res.get('goles_home')} vs {res.get('goles_away')}")
    snaps = p.get("snapshots", [])
    if snaps:
        s = snaps[-1]
        print(f"  xG: {s.get('xg_home')} vs {s.get('xg_away')}")
