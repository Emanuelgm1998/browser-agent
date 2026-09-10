import json
from urllib.parse import urlparse

with open("google_resultados.json", "r", encoding="utf-8") as f:
    datos = json.load(f)

dominios_excluir = [
    "google.com",
    "google.cl",
    "googleusercontent.com",
    "gstatic.com",
    "googleapis.com",
    "accounts.google.com",
    "support.google.com",
    "business.google.com",
    "maps.google.com",
]

limpios = []

for item in datos:
    url = item.get("url", "")
    texto = item.get("texto", "").strip()

    if not url.startswith("http"):
        continue

    dominio = urlparse(url).netloc.lower()

    if any(dominio == x or dominio.endswith("." + x) for x in dominios_excluir):
        continue

    limpios.append({
        "nombre_detectado": texto,
        "url": url,
        "dominio": dominio
    })

# Eliminar duplicados por URL
vistos = set()
finales = []

for item in limpios:
    if item["url"] not in vistos:
        vistos.add(item["url"])
        finales.append(item)

with open("fuentes_opticas.json", "w", encoding="utf-8") as f:
    json.dump(finales, f, ensure_ascii=False, indent=2)

print("========================================")
print(" FUENTES LIMPIAS")
print("========================================")
print("Encontradas:", len(finales))

for i, item in enumerate(finales, 1):
    print(f"{i}. {item['nombre_detectado']}")
    print(f"   {item['url']}")

print()
print("Archivo creado: fuentes_opticas.json")
