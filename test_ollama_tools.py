from ollama import chat

def abrir_pagina(url: str) -> str:
    return f"Página abierta correctamente: {url}"

response = chat(
    model="qwen3:4b",
    messages=[
        {
            "role": "user",
            "content": "Abre Google."
        }
    ],
    tools=[abrir_pagina],
    think=False,
)

print("===== RESPUESTA =====")
print(response.message)
print("===== TOOL CALLS =====")
print(response.message.tool_calls)
