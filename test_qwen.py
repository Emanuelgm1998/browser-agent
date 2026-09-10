from ollama import Client

client = Client(host="http://localhost:11434")

print("[TEST] Enviando petición a Qwen...")

response = client.chat(
    model="qwen3:1.7b",
    messages=[
        {
            "role": "user",
            "content": "Responde solamente OK"
        }
    ],
    options={
        "temperature": 0,
        "num_ctx": 2048,
        "num_predict": 32,
    }
)

print("[TEST] Respuesta:")
print(repr(response["message"]["content"]))