import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = Path("browser_profile")
OUTPUT = Path("google_resultados.json")

async def main():
    async with async_playwright() as p:

        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1366, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("Abriendo Google...")
        await page.goto(
            "https://www.google.com",
            wait_until="domcontentloaded",
            timeout=30000
        )

        await page.wait_for_timeout(2000)

        # Detectar bloqueo/CAPTCHA
        if "/sorry/" in page.url or "captcha" in page.url.lower():
            print()
            print("========================================")
            print(" CAPTCHA / BLOQUEO DETECTADO")
            print(" Resuélvelo manualmente en Chromium.")
            print(" NO cierres la ventana.")
            print("========================================")
            input("Cuando termines, presiona ENTER aquí...")

        print("URL actual:", page.url)

        # Si seguimos bloqueados, no continuamos
        if "/sorry/" in page.url:
            print("Google sigue bloqueando esta sesión.")
            print("Guardando sesión para el próximo intento.")
            await context.close()
            return

        buscador = page.locator(
            'textarea[name="q"], input[name="q"]'
        ).first

        await buscador.wait_for(timeout=10000)
        await buscador.fill("ópticas Chile")
        await buscador.press("Enter")

        await page.wait_for_timeout(4000)

        print("URL después de buscar:", page.url)

        if "/sorry/" in page.url:
            print()
            print("Google volvió a solicitar verificación.")
            print("Resuélvela manualmente y presiona ENTER.")
            input()

        resultados = []

        for enlace in await page.locator("a").all():
            try:
                texto = (await enlace.inner_text()).strip()
                url = await enlace.get_attribute("href")

                if texto and url and url.startswith("http"):
                    resultados.append({
                        "texto": texto,
                        "url": url
                    })
            except Exception:
                continue

        OUTPUT.write_text(
            json.dumps(
                resultados,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print()
        print("========================================")
        print(" INVESTIGACIÓN TERMINADA")
        print(" Resultados:", len(resultados))
        print(" Archivo:", OUTPUT)
        print("========================================")

        await context.close()

asyncio.run(main())
