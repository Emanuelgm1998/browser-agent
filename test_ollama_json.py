from ollama import chat

schema = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string"
        }
    },
    "required": ["url"]
}

response = chat(
    model="qwen3:1.7b",
    messages=[
        {
            "role": "user",
            "content": "Devuelve solamente un JSON con la URL de Google."
        }
    ],
    format=schema,
    think=False,
)

print("===== CONTENIDO =====")
print(repr(response.message.content))

print("===== RESPUESTA COMPLETA =====")
print(response.message)
