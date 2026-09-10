import asyncio
import json
import os
import re
import time
from datetime import datetime

from ollama import Client
from playwright.async_api import async_playwright


# ============================================================
# CONFIG
# ============================================================

MODEL = "qwen3:1.7b"
OLLAMA_HOST = "http://127.0.0.1:11434"

MAX_STEPS = 12
MAX_RETRIES = 3

BROWSER_HEADLESS = False

RUNS_DIR = "runs"

os.makedirs(RUNS_DIR, exist_ok=True)

ollama = Client(host=OLLAMA_HOST)


# ============================================================
# UTILIDADES
# ============================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_json(raw):
    if not raw:
        return None

    raw = raw.strip()

    # Intento directo
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Buscar objeto JSON dentro de texto
    match = re.search(r"\{.*\}", raw, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return None


def save_run(task, history, result):
    filename = datetime.now().strftime(
        "run_%Y%m%d_%H%M%S.json"
    )

    path = os.path.join(
        RUNS_DIR,
        filename
    )

    data = {
        "created_at": now(),
        "task": task,
        "result": result,
        "steps": history,
    }

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    return path


# ============================================================
# OBSERVADOR
# ============================================================

async def get_observation(page):

    try:
        title = await page.title()
    except Exception:
        title = ""

    try:
        url = page.url
    except Exception:
        url = ""

    try:
        body_text = await page.locator(
            "body"
        ).inner_text(timeout=5000)
    except Exception:
        body_text = ""

    try:
        buttons = await page.locator(
            "button"
        ).all_inner_texts()
    except Exception:
        buttons = []

    try:
        links = await page.locator(
            "a"
        ).all_inner_texts()
    except Exception:
        links = []

    try:
        inputs = await page.locator(
            "input, textarea, select"
        ).count()
    except Exception:
        inputs = 0

    # Detectores básicos
    lower = body_text.lower()

    captcha = any(
        x in lower
        for x in [
            "captcha",
            "verify you are human",
            "unusual traffic",
            "i'm not a robot",
            "verifica que eres humano",
        ]
    )

    blocked = any(
        x in lower
        for x in [
            "access denied",
            "403 forbidden",
            "too many requests",
            "request blocked",
        ]
    )

    return {
        "url": url,
        "title": title,
        "text": body_text[:8000],
        "buttons": buttons[:40],
        "links": links[:40],
        "inputs": inputs,
        "captcha_detected": captcha,
        "blocked_detected": blocked,
    }


# ============================================================
# OLLAMA
# ============================================================

def qwen_request(
    messages,
    num_ctx=4096,
    num_predict=300
):

    response = ollama.chat(
        model=MODEL,
        think=False,
        messages=messages,
        format="json",
        options={
            "temperature": 0,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    )

    return response


# ============================================================
# CEREBRO
# ============================================================

async def ask_qwen(
    task,
    observation,
    history
):

    prompt = f"""
You are the decision engine of a web browser automation agent.

USER TASK:
{task}

CURRENT BROWSER STATE:
{json.dumps(
    observation,
    ensure_ascii=False,
    indent=2
)}

RECENT HISTORY:
{json.dumps(
    history[-5:],
    ensure_ascii=False,
    indent=2
)}

Choose exactly ONE action.

AVAILABLE ACTIONS:

NAVIGATE:
{{
  "action": "navigate",
  "url": "https://example.com"
}}

CLICK:
{{
  "action": "click",
  "selector": "visible text or CSS selector"
}}

TYPE:
{{
  "action": "type",
  "selector": "input[name='q']",
  "text": "hello"
}}

EXTRACT:
{{
  "action": "extract",
  "selector": "body"
}}

BACK:
{{
  "action": "back"
}}

FINISH:
{{
  "action": "finish",
  "result": "final answer"
}}

WAIT:
{{
  "action": "wait",
  "seconds": 2
}}

RULES:

1. Return ONLY valid JSON.
2. Never return markdown.
3. Never explain your decision.
4. Execute only ONE action.
5. Do not invent information.
6. Use only information visible in the browser.
7. If the task is complete, use finish.
8. If a CAPTCHA or human verification is detected, use finish and clearly report that human intervention is required.
9. If the page is blocked, use finish and report the block.
10. Prefer visible text for clicking.
11. Do not repeat the same failed action indefinitely.
12. Keep the response extremely short.
"""

    messages = [
        {
            "role": "system",
            "content": (
                "You are a browser automation controller. "
                "Output only valid JSON."
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            print(
                f"\n[QWEN] Intento {attempt}/{MAX_RETRIES}"
            )

            response = await asyncio.to_thread(
                qwen_request,
                messages,
                4096,
                300
            )

            raw = response[
                "message"
            ].get(
                "content",
                ""
            )

            print(
                "[QWEN RAW]",
                repr(raw)
            )

            data = clean_json(raw)

            if not data:
                print(
                    "[QWEN] JSON inválido."
                )
                continue

            allowed = {
                "navigate",
                "click",
                "type",
                "extract",
                "back",
                "finish",
                "wait",
            }

            action = data.get(
                "action"
            )

            if action not in allowed:
                print(
                    "[QWEN] Acción inválida:",
                    action
                )
                continue

            print(
                "[QWEN DECISION]",
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                )
            )

            return data

        except Exception as e:

            print(
                "[QWEN ERROR]",
                type(e).__name__,
                str(e)
            )

            await asyncio.sleep(1)

    return None


# ============================================================
# EJECUTOR
# ============================================================

async def execute_action(
    page,
    action
):

    action_type = action.get(
        "action"
    )

    # --------------------------------------------------------
    # NAVIGATE
    # --------------------------------------------------------

    if action_type == "navigate":

        url = action.get(
            "url"
        )

        if not url:
            return "ERROR: URL vacía"

        print(
            f"[BROWSER] NAVIGATE -> {url}"
        )

        try:

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000
            )

            await page.wait_for_timeout(
                1000
            )

            return (
                f"Navegación completada. "
                f"URL={page.url}; "
                f"Título={await page.title()}"
            )

        except Exception as e:

            return (
                f"ERROR navigate: {e}"
            )

    # --------------------------------------------------------
    # CLICK
    # --------------------------------------------------------

    if action_type == "click":

        selector = action.get(
            "selector"
        )

        if not selector:
            return "ERROR: selector vacío"

        print(
            f"[BROWSER] CLICK -> {selector}"
        )

        try:

            # Primero texto visible
            locator = page.get_by_text(
                selector,
                exact=False
            ).first

            if await locator.count() == 0:

                locator = page.locator(
                    selector
                ).first

            await locator.click(
                timeout=10000
            )

            await page.wait_for_timeout(
                1000
            )

            return "Click realizado"

        except Exception as e:

            return (
                f"ERROR click: {e}"
            )

    # --------------------------------------------------------
    # TYPE
    # --------------------------------------------------------

    if action_type == "type":

        selector = action.get(
            "selector"
        )

        text = action.get(
            "text",
            ""
        )

        if not selector:
            return "ERROR: selector vacío"

        print(
            f"[BROWSER] TYPE -> {selector}"
        )

        try:

            locator = page.locator(
                selector
            ).first

            await locator.fill(
                text,
                timeout=10000
            )

            return "Texto introducido"

        except Exception as e:

            return (
                f"ERROR type: {e}"
            )

    # --------------------------------------------------------
    # EXTRACT
    # --------------------------------------------------------

    if action_type == "extract":

        selector = action.get(
            "selector",
            "body"
        )

        print(
            f"[BROWSER] EXTRACT -> {selector}"
        )

        try:

            locator = page.locator(
                selector
            )

            text = await locator.inner_text(
                timeout=10000
            )

            return text[:8000]

        except Exception as e:

            return (
                f"ERROR extract: {e}"
            )

    # --------------------------------------------------------
    # BACK
    # --------------------------------------------------------

    if action_type == "back":

        print(
            "[BROWSER] BACK"
        )

        try:

            await page.go_back(
                wait_until="domcontentloaded",
                timeout=30000
            )

            return "Volvió a la página anterior"

        except Exception as e:

            return (
                f"ERROR back: {e}"
            )

    # --------------------------------------------------------
    # WAIT
    # --------------------------------------------------------

    if action_type == "wait":

        seconds = action.get(
            "seconds",
            2
        )

        try:
            seconds = float(seconds)
        except Exception:
            seconds = 2

        seconds = max(
            0,
            min(seconds, 10)
        )

        print(
            f"[BROWSER] WAIT -> {seconds}s"
        )

        await asyncio.sleep(
            seconds
        )

        return (
            f"Esperó {seconds} segundos"
        )

    # --------------------------------------------------------
    # FINISH
    # --------------------------------------------------------

    if action_type == "finish":

        return None

    return (
        f"ERROR: acción desconocida "
        f"{action_type}"
    )


# ============================================================
# AGENT LOOP
# ============================================================

async def run_agent(task):

    start = time.time()

    print()
    print("=" * 70)
    print("          BROWSER AGENT IA V2")
    print("=" * 70)
    print()
    print("[AGENT] Modelo:", MODEL)
    print("[AGENT] Tarea:", task)
    print()

    history = []

    result = None

    async with async_playwright() as p:

        print(
            "[BROWSER] Iniciando Chromium..."
        )

        browser = await p.chromium.launch(
            headless=BROWSER_HEADLESS
        )

        page = await browser.new_page()

        try:

            for step in range(
                1,
                MAX_STEPS + 1
            ):

                print()
                print(
                    "=" * 20,
                    f" PASO {step} ",
                    "=" * 20
                )

                observation = (
                    await get_observation(
                        page
                    )
                )

                print(
                    "[OBSERVER] URL:",
                    observation["url"]
                )

                print(
                    "[OBSERVER] Título:",
                    observation["title"]
                )

                # CAPTCHA
                if observation[
                    "captcha_detected"
                ]:

                    result = (
                        "CAPTCHA o verificación "
                        "humana detectada. "
                        "Se requiere intervención "
                        "del usuario."
                    )

                    print(
                        "[SECURITY]",
                        result
                    )

                    break

                # BLOCK
                if observation[
                    "blocked_detected"
                ]:

                    result = (
                        "La página parece estar "
                        "bloqueada o rechazó "
                        "la solicitud."
                    )

                    print(
                        "[SECURITY]",
                        result
                    )

                    break

                action = await ask_qwen(
                    task,
                    observation,
                    history
                )

                if not action:

                    result = (
                        "El modelo no produjo "
                        "una acción válida."
                    )

                    print(
                        "[AGENT]",
                        result
                    )

                    break

                # FINISH
                if action.get(
                    "action"
                ) == "finish":

                    result = action.get(
                        "result",
                        "Tarea completada."
                    )

                    print(
                        "[AGENT] Tarea completada."
                    )

                    break

                execution_result = (
                    await execute_action(
                        page,
                        action
                    )
                )

                print(
                    "[RESULTADO]",
                    execution_result
                )

                history.append(
                    {
                        "timestamp": now(),
                        "step": step,
                        "action": action,
                        "result": execution_result,
                        "url": page.url,
                    }
                )

            else:

                result = (
                    "Se alcanzó el máximo "
                    "de pasos."
                )

        except Exception as e:

            result = (
                f"Error crítico del agente: "
                f"{type(e).__name__}: {e}"
            )

            print(
                "[AGENT ERROR]",
                result
            )

        finally:

            print()
            print(
                "[BROWSER] Cerrando Chromium..."
            )

            try:
                await browser.close()
            except Exception:
                pass

    elapsed = round(
        time.time() - start,
        2
    )

    if result is None:
        result = (
            "El agente terminó sin resultado."
        )

    run_file = save_run(
        task,
        history,
        result
    )

    print()
    print("=" * 70)
    print("                    RESULTADO")
    print("=" * 70)
    print()
    print(result)
    print()
    print("Pasos ejecutados:", len(history))
    print("Tiempo:", elapsed, "segundos")
    print("Log:", run_file)
    print()
    print("[AGENT] Finalizado.")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        task = input(
            "\n¿Qué quieres que haga el agente?\n> "
        ).strip()

        if not task:

            print(
                "No se ingresó ninguna tarea."
            )

        else:

            asyncio.run(
                run_agent(task)
            )

    except KeyboardInterrupt:

        print(
            "\n[AGENT] Detenido por usuario."
        )

