"""Drivers para interactuar con ChatGPT y Claude desde el navegador.

Usa un perfil persistente separado (browser_profile_chat/) para aislar
el riesgo de baneo de las cuentas de trabajo.
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

# ── Config ──────────────────────────────────────────────────────────────────

CHAT_PROFILE = Path(__file__).parent / "browser_profile_chat"

STREAM_TIMEOUT_MS = 200_000  # tiempo máximo de espera para streaming
PAGE_TIMEOUT_MS = 40_000
NAV_TIMEOUT_MS = 30_000
EXTRA_BUFFER_MS = 2000

# ── Excepción ───────────────────────────────────────────────────────────────

class LoginRequired(Exception):
    def __init__(self, service: str):
        self.service = service
        super().__init__(
            f"No se detectó sesión activa en {service}. "
            "Ejecuta setup_cuentas.py y haz login manual una vez."
        )

# ── Driver principal ────────────────────────────────────────────────────────

class ChatBrain:
    """Manejador de sesiones para ChatGPT y Claude.

    Ejemplo::

        async with ChatBrain() as brain:
            r1 = await brain.chatgpt("Explícame quantum computing en 3 pasos")
            r2 = await brain.claude(f"Refíname este plan:\n{r1}")
    """

    def __init__(self, profile_dir: str | Path | None = None):
        self.profile_dir = Path(profile_dir or CHAT_PROFILE)
        self._pw = None
        self._context = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self

    async def __aexit__(self, *exc):
        try:
            await self._context.close()
        except Exception:
            pass
        if self._pw:
            await self._pw.stop()

    # ── ChatGPT ─────────────────────────────────────────────────────────

    async def chatgpt(self, text: str, new_chat: bool = True) -> str:
        """Envía un mensaje a ChatGPT y devuelve la respuesta."""
        page = await self._get_page()

        # Ir a chatgpt.com
        await page.goto(
            "https://chatgpt.com/",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        await page.wait_for_timeout(2000)

        # Verificar login
        if await self._needs_login_chatgpt(page):
            raise LoginRequired("ChatGPT")

        # Nuevo chat si se pide
        if new_chat:
            await self._new_chat_chatgpt(page)
            await page.wait_for_timeout(1000)

        # Escribir en el textarea
        textarea = page.locator("#prompt-textarea")
        try:
            await textarea.wait_for(state="visible", timeout=10_000)
        except Exception:
            raise LoginRequired("ChatGPT (textarea no encontrado)")

        await textarea.click()
        await page.keyboard.insert_text(text)

        # Enviar: intentar botón send, fallback Enter
        sent = await self._click_send_button(
            page,
            [
                '[data-testid="send-button"]',
                'button[aria-label="Send prompt"]',
            ],
        )
        if not sent:
            await page.keyboard.press("Enter")

        # Esperar fin de streaming
        await self._wait_stream_end(
            page,
            stop_sel='[data-testid="stop-button"]',
        )

        # Extraer respuesta del asistente
        return await self._extract_last_message(
            page,
            selector_candidates=[
                '[data-message-author-role="assistant"] .markdown',
                '[data-message-author-role="assistant"]',
            ],
        )

    async def _new_chat_chatgpt(self, page):
        """Intenta iniciar un chat nuevo en ChatGPT."""
        # Botón "New chat" — varios selectores posibles según versión
        btn_selectors = [
            '[data-testid="new-chat-button"]',
            'a[aria-label="New chat"]',
            'a[aria-label="Chat new"]',
            'button[aria-label="New chat"]',
        ]
        for sel in btn_selectors:
            btn = page.locator(sel).first
            try:
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    return
            except Exception:
                continue
        # Si no encontramos el botón, la navegación a / ya abre un chat nuevo

    async def _needs_login_chatgpt(self, page) -> bool:
        url = page.url.lower()
        if "auth.openai.com" in url or "login" in url:
            return True
        body = ""
        try:
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            pass
        body_lower = body.lower()
        textarea = page.locator("#prompt-textarea")
        try:
            if await textarea.count() == 0 and (
                "log in" in body_lower or "sign up" in body_lower
            ):
                return True
        except Exception:
            pass
        return False

    # ── Claude ───────────────────────────────────────────────────────────

    async def claude(self, text: str) -> str:
        """Envía un mensaje a Claude y devuelve la respuesta."""
        page = await self._get_page()

        # Ir a chat nuevo
        await page.goto(
            "https://claude.ai/new",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        await page.wait_for_timeout(2500)

        # Verificar login
        if await self._needs_login_claude(page):
            raise LoginRequired("Claude")

        # Encontrar el composer
        composer = page.locator(
            '.ProseMirror[contenteditable="true"]'
        ).first

        try:
            await composer.wait_for(state="visible", timeout=10_000)
        except Exception:
            raise LoginRequired("Claude (textarea no encontrado)")

        await composer.click()
        await page.keyboard.insert_text(text)

        # Enviar: botón Send o Ctrl+Enter
        sent = await self._click_send_button(
            page,
            [
                'button[aria-label*="Send"]',
                'button[aria-label*="Enviar"]',
            ],
        )
        if not sent:
            await page.keyboard.press("Control+Enter")

        # Esperar fin de streaming
        await self._wait_stream_end(
            page,
            stop_sel='button[aria-label*="Stop"], button[aria-label*="Detener"]',
        )

        # Extraer respuesta de Claude
        return await self._extract_last_message(
            page,
            selector_candidates=[
                ".font-claude-message",
                '[data-testid*="assistant-message"]',
                '[data-testid*="message"]',
            ],
        )

    async def _needs_login_claude(self, page) -> bool:
        url = page.url.lower()
        if "claude.ai/login" in url or "claude.ai/auth" in url:
            return True
        body = ""
        try:
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            pass
        body_lower = body.lower()
        composer = page.locator('.ProseMirror[contenteditable="true"]')
        try:
            if await composer.count() == 0 and (
                "log in" in body_lower or "iniciar sesión" in body_lower
            ):
                return True
        except Exception:
            pass
        return False

    # ── Utilidades internas ──────────────────────────────────────────────

    async def _get_page(self):
        """Retorna la primera pestaña del contexto reutilizándola."""
        if not self._context:
            raise RuntimeError("ChatBrain no inicializado. Usa 'async with ChatBrain() as brain:'")
        if self._context.pages:
            return self._context.pages[0]
        return await self._context.new_page()

    async def _click_send_button(self, page, selectors: list[str]) -> bool:
        """Intenta hacer click en un botón de envío. Devuelve True si lo encontró."""
        for sel in selectors:
            btn = page.locator(sel).first
            try:
                if await btn.count() > 0:
                    # Esperar que esté habilitado
                    await btn.wait_for(state="visible", timeout=3000)
                    await asyncio.sleep(0.5)
                    await btn.click(timeout=3000)
                    return True
            except Exception:
                continue
        return False

    async def _wait_stream_end(self, page, stop_sel: str):
        """Espera que termine el streaming de la respuesta."""
        # Esperar a que aparezca el botón de parar
        stop_btn = page.locator(stop_sel).first
        try:
            await stop_btn.wait_for(state="visible", timeout=15_000)
        except Exception:
            # Puede que la respuesta sea instantánea o el botón no aparezca
            pass

        # Esperar a que desaparezca
        try:
            await stop_btn.wait_for(state="detached", timeout=STREAM_TIMEOUT_MS)
        except Exception:
            pass

        # Buffer adicional para que el DOM se asiente
        await page.wait_for_timeout(EXTRA_BUFFER_MS)

    async def _extract_last_message(self, page, selector_candidates: list[str]) -> str:
        """Extrae el texto del último mensaje de asistente en la página."""
        for sel in selector_candidates:
            locator = page.locator(sel)
            try:
                count = await locator.count()
                if count > 0:
                    text = await locator.last.inner_text(timeout=5000)
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue

        # Fallback: intentar obtener cualquier contenido del chat
        try:
            body = await page.locator("main").inner_text(timeout=5000)
            if body:
                return body.strip()
        except Exception:
            pass

        return "(no se pudo extraer la respuesta)"

# ── Script de prueba ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _test():
        msg = (
            sys.argv[1]
            if len(sys.argv) > 1
            else "Responde únicamente: OK"
        )

        async with ChatBrain() as brain:
            print("[TEST] Probando ChatGPT...")
            r1 = await brain.chatgpt(msg)
            print(f"[CHATGPT] {r1[:300]}")

            print("\n[TEST] Probando Claude...")
            r2 = await brain.claude(msg)
            print(f"[CLAUDE] {r2[:300]}")

            print("\n[TEST] ChatGPT + Claude: OK")

    asyncio.run(_test())
