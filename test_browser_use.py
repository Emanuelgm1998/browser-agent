from browser_use import Agent
from browser_use.llm import ChatOllama
import asyncio

async def main():
    print("=== BROWSER USE + QWEN3 TEST ===")
    print("Modelo: qwen3:1.7b")
    print("Objetivo: abrir example.com y obtener el título")
    print()

    llm = ChatOllama(
        model="qwen3:1.7b",
        host="http://127.0.0.1:11434",
        timeout=120,
        ollama_options={
            "num_ctx": 4096,
            "num_predict": 256,
            "temperature": 0,
        },
    )

    agent = Agent(
        task="""
Open https://example.com
Find the page title.
Return the title and the URL.
Then stop.
""",
        llm=llm,
        use_vision=False,
        use_thinking=False,
        max_actions_per_step=1,
        max_failures=2,
        llm_timeout=120,
        step_timeout=180,
        enable_planning=False,
        use_judge=False,
    )

    try:
        history = await agent.run()

        print()
        print("=== RESULTADO ===")
        print(history)

    except Exception as e:
        print()
        print("=== ERROR ===")
        print(type(e).__name__)
        print(str(e))

asyncio.run(main())
