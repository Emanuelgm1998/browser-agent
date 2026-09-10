from ollama import chat
import json

with open("investigacion_opticas.json", "r", encoding="utf-8") as f:
    datos = json.load(f)

texto = json.dumps(datos, ensure_ascii=False)

prompt = f"""
Analiza esta investigación de una óptica chilena.

Extrae solamente información útil para construir una demo profesional de ecommerce óptico.

Devuelve JSON con exactamente esta estructura:

{{
  "marca": "",
  "instagram": "",
  "seguidores": "",
  "categorias": [],
  "servicios": [],
  "beneficios": [],
  "publico_objetivo": [],
  "contacto": {{
    "telefono": "",
    "email": "",
    "direccion": ""
  }},
  "horarios": "",
  "elementos_para_demo": [],
  "observaciones": []
}}

No inventes información.
Si un dato no aparece, déjalo vacío.

INVESTIGACIÓN:
{texto}
"""

schema = {
    "type": "object",
    "properties": {
        "marca": {"type": "string"},
        "instagram": {"type": "string"},
        "seguidores": {"type": "string"},
        "categorias": {"type": "array", "items": {"type": "string"}},
        "servicios": {"type": "array", "items": {"type": "string"}},
        "beneficios": {"type": "array", "items": {"type": "string"}},
        "publico_objetivo": {"type": "array", "items": {"type": "string"}},
        "contacto": {
            "type": "object",
            "properties": {
                "telefono": {"type": "string"},
                "email": {"type": "string"},
                "direccion": {"type": "string"}
            },
            "required": ["telefono", "email", "direccion"]
        },
        "horarios": {"type": "string"},
        "elementos_para_demo": {"type": "array", "items": {"type": "string"}},
        "observaciones": {"type": "array", "items": {"type": "string"}}
    },
    "required": [
        "marca",
        "instagram",
        "seguidores",
        "categorias",
        "servicios",
        "beneficios",
        "publico_objetivo",
        "contacto",
        "horarios",
        "elementos_para_demo",
        "observaciones"
    ]
}

respuesta = chat(
    model="qwen3:1.7b",
    messages=[{"role": "user", "content": prompt}],
    format=schema,
    think=False
)

resultado = json.loads(respuesta.message.content)

with open("perfil_optica.json", "w", encoding="utf-8") as f:
    json.dump(resultado, f, ensure_ascii=False, indent=2)

print(json.dumps(resultado, ensure_ascii=False, indent=2))
print()
print("Archivo creado: perfil_optica.json")
