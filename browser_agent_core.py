import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("Abriendo Google...")
        await page.goto(
            "https://www.google.com",
            wait_until="domcontentloaded",
            timeout=30000
        )

        print("Google abierto:", await page.title())

        buscador = page.locator('textarea[name="q"], input[name="q"]').first
        await buscador.wait_for(timeout=10000)
        await buscador.fill("ópticas Chile")
        await buscador.press("Enter")

        # Esperar a que la navegación termine
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except:
            pass

        await page.wait_for_timeout(3000)

        print("URL:", page.url)
        print("Título:", await page.title())

        # Obtener los enlaces uno por uno
        resultados = []

        enlaces = await page.locator("a").all()

        for enlace in enlaces[:50]:
            try:
                texto = (await enlace.inner_text()).strip()
                url = await enlace.get_attribute("href")

                if texto and url:
                    resultados.append({
                        "texto": texto,
                        "url": url
                    })
            except:
                continue

        with open(
            "google_resultados.json",
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                resultados,
                f,
                ensure_ascii=False,
                indent=2
            )

        print()
        print("===== RESULTADO =====")
        print("Enlaces encontrados:", len(resultados))
        print("Archivo creado: google_resultados.json")

        for r in resultados[:10]:
            print("-", r["texto"][:80])
            print(" ", r["url"])

        await asyncio.sleep(5)
        await browser.close()

asyncio.run(main())
