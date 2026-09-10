import asyncio
from browser_use import Agent, ChatOllama

async def main():
    llm = ChatOllama(
        model="qwen3:1.7b",
        timeout=120,
        ollama_options={
            "num_ctx": 4096,
            "num_predict": 128,
            "temperature": 0,
        },
    )

    agent = Agent(
        task="Open https://www.google.com and tell me the page title. Then stop.",
        llm=llm,
        use_vision=False,
        use_thinking=False,
        max_actions_per_step=1,
        llm_timeout=120,
        step_timeout=180,
    )

    result = await agent.run(max_steps=2)

    print("\n===== RESULTADO =====")
    print(result)

asyncio.run(main())
