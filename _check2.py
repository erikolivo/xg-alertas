import json
data = json.loads(open("data/partidos_hoy.json", "r", encoding="utf-8").read())
print(f"Total: {len(data.get('partidos', []))} partidos")
print(f"Fecha: {data.get('fecha')}")
