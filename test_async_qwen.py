import asyncio
from ollama import AsyncClient


async def main():
    client = AsyncClient(host="http://localhost:11434")

    response = await client.chat(
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

    print("RAW:")
    print(repr(response["message"]["content"]))


asyncio.run(main())