from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)


BASE_DIR = Path(__file__).resolve().parent

PROFILE_DIR = BASE_DIR / "browser_profile_chat"

CHATGPT_URL = "https://chatgpt.com/"
CLAUDE_URL = "https://claude.ai/"

# ------------------------------------------------------------
# CONFIGURACION RAPIDA
# ------------------------------------------------------------

POLL_INTERVAL = 0.30
STABLE_INTERVAL = 0.45

PAGE_TIMEOUT = 20_000
RESPONSE_TIMEOUT = 120_000

# Evita volver a esperar segundos completos cuando
# la pagina ya esta cargada.
FAST_WAIT = 250

# Numero de comprobaciones consecutivas con texto identico
# para considerar que el streaming termino.
STABLE_CHECKS = 8

# Palabras que indican que el modelo todavia esta "pensando"/generando
# (observadas en la UI real de ChatGPT y Claude: "Pensar", "Triangulando").
# Si aparecen al final del texto, NO se considera respuesta estable
# aunque el texto no haya cambiado en varios polls.
GENERATING_MARKERS = [
    "Pensar",
    "Pensando",
    "Triangulando",
    "Analizando",
    "Razonando",
    "Thinking",
]


class LoginRequired(RuntimeError):
    pass


class ChatBrain:
    """
    Dual Brain browser driver.

    Mantiene una unica instancia de Chromium y reutiliza
    las paginas de ChatGPT y Claude durante toda la sesion.

    Objetivo:
        ChatGPT -> Claude -> ChatGPT

    sin cerrar/reabrir el navegador entre llamadas.
    """

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        headless: bool = False,
    ):
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.headless = headless

        self.playwright = None
        self.context: Optional[BrowserContext] = None

        self.chatgpt_page: Optional[Page] = None
        self.claude_page: Optional[Page] = None

        self._started = False

    # --------------------------------------------------------
    # CONTEXT MANAGER
    # --------------------------------------------------------

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    async def start(self):
        if self._started and self.context:
            return

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self.playwright = await async_playwright().start()

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        self._started = True

        # Reutilizar paginas existentes si existen.
        pages = list(self.context.pages)

        for page in pages:
            try:
                url = page.url.lower()

                if "chatgpt.com" in url and self.chatgpt_page is None:
                    self.chatgpt_page = page

                elif "claude.ai" in url and self.claude_page is None:
                    self.claude_page = page
            except Exception:
                pass

        # Crear/navegar solamente cuando sea necesario.
        if self.chatgpt_page is None:
            self.chatgpt_page = await self.context.new_page()

        if self.claude_page is None:
            self.claude_page = await self.context.new_page()

        await self._ensure_chatgpt_ready()
        await self._ensure_claude_ready()

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = None
            self.chatgpt_page = None
            self.claude_page = None
            self._started = False

            if self.playwright:
                await self.playwright.stop()

            self.playwright = None

    # --------------------------------------------------------
    # PAGE READY
    # --------------------------------------------------------

    async def _ensure_chatgpt_ready(self):
        if not self.chatgpt_page:
            raise RuntimeError("ChatGPT page no disponible.")

        page = self.chatgpt_page

        start = time.perf_counter()

        if "chatgpt.com" not in page.url.lower():
            await page.goto(
                CHATGPT_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

        await self._wait_page_interactive(page)

        if "/auth/" in page.url.lower() or "/login" in page.url.lower():
            raise LoginRequired(
                "ChatGPT requiere login. Inicia sesion manualmente "
                "en browser_profile_chat."
            )

        # Selector actual y fallback.
        selectors = [
            "#prompt-textarea",
            "textarea",
            "div[contenteditable='true']",
        ]

        if not await self._wait_for_any(page, selectors, 8):
            raise LoginRequired(
                "No se encontro el cuadro de ChatGPT. "
                "Puede requerir login."
            )

        elapsed = time.perf_counter() - start

        print(
            f"[ChatGPT] listo en {elapsed:.2f}s",
            flush=True,
        )

    async def _ensure_claude_ready(self):
        if not self.claude_page:
            raise RuntimeError("Claude page no disponible.")

        page = self.claude_page

        start = time.perf_counter()

        if "claude.ai" not in page.url.lower():
            await page.goto(
                CLAUDE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

        await self._wait_page_interactive(page)

        if "/login" in page.url.lower():
            raise LoginRequired(
                "Claude requiere login. Inicia sesion manualmente "
                "en browser_profile_chat."
            )

        selectors = [
            "div[contenteditable='true']",
            "textarea",
        ]

        if not await self._wait_for_any(page, selectors, 8):
            raise LoginRequired(
                "No se encontro el cuadro de Claude. "
                "Puede requerir login."
            )

        elapsed = time.perf_counter() - start

        print(
            f"[Claude] listo en {elapsed:.2f}s",
            flush=True,
        )

    # --------------------------------------------------------
    # FAST HELPERS
    # --------------------------------------------------------

    async def _wait_page_interactive(self, page: Page):
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )
        except Exception:
            pass

        # Pequeña espera solo para permitir render inicial.
        await page.wait_for_timeout(FAST_WAIT)

    async def _wait_for_any(
        self,
        page: Page,
        selectors: list[str],
        timeout_seconds: float,
    ) -> bool:

        deadline = time.perf_counter() + timeout_seconds

        while time.perf_counter() < deadline:

            for selector in selectors:
                try:
                    locator = page.locator(selector).first

                    if await locator.is_visible(timeout=150):
                        return True

                except Exception:
                    pass

            await asyncio.sleep(POLL_INTERVAL)

        return False

    async def _first_visible(
        self,
        page: Page,
        selectors: list[str],
    ):
        for selector in selectors:
            try:
                locator = page.locator(selector).first

                if await locator.is_visible(timeout=200):
                    return locator
            except Exception:
                pass

        return None

    # --------------------------------------------------------
    # TEXT EXTRACTION
    # --------------------------------------------------------

    async def _body_text(self, page: Page) -> str:
        try:
            return await page.locator("body").inner_text(
                timeout=2_000
            )
        except Exception:
            return ""

    async def _wait_response_stable(
        self,
        page: Page,
        baseline: str,
    ) -> str:

        start = time.perf_counter()

        last_text = baseline
        stable_count = 0

        while time.perf_counter() - start < RESPONSE_TIMEOUT / 1000:

            await asyncio.sleep(POLL_INTERVAL)

            try:
                current = await self._body_text(page)
            except Exception:
                continue

            if not current:
                continue

            if current == last_text:
                stable_count += 1
            else:
                stable_count = 0
                last_text = current

            # Respuesta estabilizada.
            if stable_count >= STABLE_CHECKS:
                return current

        return last_text

    # --------------------------------------------------------
    # CHATGPT
    # --------------------------------------------------------

    async def chatgpt(
        self,
        text: str,
        new_chat: bool = True,
    ) -> str:

        if not self._started:
            await self.start()

        page = self.chatgpt_page

        if not page:
            raise RuntimeError("ChatGPT page no disponible.")

        total_start = time.perf_counter()

        print("[ChatGPT] preparando prompt...", flush=True)

        # Para esta fase usamos una conversacion nueva.
        # No recargamos el sitio completo.
        if new_chat:
            try:
                new_chat_selectors = [
                    "a[href='/']",
                    "a[href='/?oai-dm=1']",
                    "button:has-text('New chat')",
                    "[aria-label*='New chat']",
                ]

                button = await self._first_visible(
                    page,
                    new_chat_selectors,
                )

                if button:
                    await button.click()
                    await page.wait_for_timeout(FAST_WAIT)

            except Exception:
                pass

        input_selectors = [
            "#prompt-textarea",
            "textarea",
            "div[contenteditable='true']",
        ]

        input_box = await self._first_visible(
            page,
            input_selectors,
        )

        if input_box is None:
            await self._ensure_chatgpt_ready()

            input_box = await self._first_visible(
                page,
                input_selectors,
            )

        if input_box is None:
            raise LoginRequired(
                "No se encontro input de ChatGPT."
            )

        before = await self._body_text(page)

        send_start = time.perf_counter()

        try:
            await input_box.fill(text)
        except Exception:
            await input_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(text)

        # Enter envia.
        await input_box.press("Enter")

        send_elapsed = time.perf_counter() - send_start

        print(
            f"[ChatGPT] prompt enviado en {send_elapsed:.2f}s",
            flush=True,
        )

        response = await self._wait_chatgpt_response(
            page,
            before,
        )

        total = time.perf_counter() - total_start

        print(
            f"[ChatGPT] respuesta recibida en {total:.2f}s",
            flush=True,
        )

        return response

    async def _wait_chatgpt_response(
        self,
        page: Page,
        before: str,
    ) -> str:

        start = time.perf_counter()

        last = before
        stable = 0

        while time.perf_counter() - start < RESPONSE_TIMEOUT / 1000:

            await asyncio.sleep(POLL_INTERVAL)

            try:
                current = await self._body_text(page)
            except Exception:
                continue

            if not current:
                continue

            if current == last:
                stable += 1
            else:
                stable = 0
                last = current

            # Evitar considerar inmediatamente el body viejo.
            # Tambien evitar cortar mientras el modelo sigue "pensando".
            if (
                current != before
                and stable >= STABLE_CHECKS
                and not self._looks_like_still_generating(current)
            ):
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # CLAUDE
    # --------------------------------------------------------

    async def claude(self, text: str) -> str:

        if not self._started:
            await self.start()

        page = self.claude_page

        if not page:
            raise RuntimeError("Claude page no disponible.")

        total_start = time.perf_counter()

        print("[Claude] preparando prompt...", flush=True)

        input_selectors = [
            "div[contenteditable='true']",
            "textarea",
        ]

        input_box = await self._first_visible(
            page,
            input_selectors,
        )

        if input_box is None:
            await self._ensure_claude_ready()

            input_box = await self._first_visible(
                page,
                input_selectors,
            )

        if input_box is None:
            raise LoginRequired(
                "No se encontro input de Claude."
            )

        before = await self._body_text(page)

        send_start = time.perf_counter()

        try:
            await input_box.fill(text)
        except Exception:
            await input_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(text)

        await input_box.press("Enter")

        send_elapsed = time.perf_counter() - send_start

        print(
            f"[Claude] prompt enviado en {send_elapsed:.2f}s",
            flush=True,
        )

        response = await self._wait_claude_response(
            page,
            before,
        )

        total = time.perf_counter() - total_start

        print(
            f"[Claude] respuesta recibida en {total:.2f}s",
            flush=True,
        )

        return response

    async def _wait_claude_response(
        self,
        page: Page,
        before: str,
    ) -> str:

        start = time.perf_counter()

        last = before
        stable = 0

        while time.perf_counter() - start < RESPONSE_TIMEOUT / 1000:

            await asyncio.sleep(POLL_INTERVAL)

            try:
                current = await self._body_text(page)
            except Exception:
                continue

            if not current:
                continue

            if current == last:
                stable += 1
            else:
                stable = 0
                last = current

            if (
                current != before
                and stable >= STABLE_CHECKS
                and not self._looks_like_still_generating(current)
            ):
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # DETECCION DE "SIGUE GENERANDO"
    # --------------------------------------------------------

    def _looks_like_still_generating(self, text: str) -> bool:
        """
        Heuristica: si alguna palabra de GENERATING_MARKERS aparece
        cerca del final del texto visible, asumimos que el modelo
        todavia esta en estado de "pensando" y NO se debe considerar
        la respuesta como estable/completa todavia.
        """
        if not text:
            return False
        cola = text[-300:]
        return any(marker in cola for marker in GENERATING_MARKERS)

    # --------------------------------------------------------
    # RESPONSE EXTRACTION
    # --------------------------------------------------------

    def _extract_latest_response(
        self,
        current: str,
        before: str,
    ) -> str:

        if not current:
            return ""

        if not before:
            return current.strip()

        # Diferencia simple contra el body anterior.
        if current.startswith(before):
            result = current[len(before):].strip()

            if result:
                return result

        # Fallback: devolver body completo.
        return current.strip()

    # --------------------------------------------------------
    # COMPATIBILITY WRAPPERS
    # --------------------------------------------------------

    async def ask_chatgpt(self, text: str) -> str:
        """
        Compatibilidad con reasoning_loop.py.
        """
        return await self.chatgpt(text)

    async def ask_claude(self, text: str) -> str:
        """
        Compatibilidad con reasoning_loop.py.
        """
        return await self.claude(text)


# ------------------------------------------------------------
# TEST DIRECTO
# ------------------------------------------------------------

async def _test():

    print("")
    print("=" * 60)
    print(" CHAT BRAIN FAST - TEST")
    print("=" * 60)
    print("")

    start = time.perf_counter()

    async with ChatBrain() as brain:

        print("")
        print("[TEST] ChatGPT...")
        print("")

        response1 = await brain.ask_chatgpt(
            "Responde solamente: CHATGPT_OK"
        )

        print("")
        print("CHATGPT RESULT:")
        print(response1[:1000])

        print("")
        print("[TEST] Claude...")
        print("")

        response2 = await brain.ask_claude(
            "Responde solamente: CLAUDE_OK"
        )

        print("")
        print("CLAUDE RESULT:")
        print(response2[:1000])

    total = time.perf_counter() - start

    print("")
    print("=" * 60)
    print(f"TIEMPO TOTAL: {total:.2f}s")
    print("=" * 60)
    print("")


if __name__ == "__main__":
    asyncio.run(_test())
