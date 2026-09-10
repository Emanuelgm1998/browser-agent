from browser_use import Agent
from browser_use.llm import ChatOllama
import asyncio

async def main():
    print("=== BROWSER USE FINAL TEST ===")
    print("Modelo: qwen3:1.7b")
    print()

    llm = ChatOllama(
        model="qwen3:1.7b",
        host="http://127.0.0.1:11434",
        timeout=180,
        ollama_options={
            "num_ctx": 2048,
            "num_predict": 512,
            "temperature": 0,
        },
    )

    agent = Agent(
        task="""
Open https://example.com.
Read the page.
Return:
1. The page title.
2. The URL.
3. The main heading.
Then stop.
""",
        llm=llm,
        use_vision=False,
        use_thinking=False,
        max_actions_per_step=1,
        max_failures=1,
        llm_timeout=180,
        step_timeout=240,
        enable_planning=False,
        use_judge=False,
        flash_mode=True,
        max_history_items=6,
    )

    try:
        history = await agent.run()

        print()
        print("================================")
        print("       RESULTADO FINAL")
        print("================================")
        print(history)

    except Exception as e:
        print()
        print("================================")
        print("             ERROR")
        print("================================")
        print(type(e).__name__)
        print(str(e))

asyncio.run(main())
