import asyncio
import json
from urllib.parse import urljoin
from playwright.async_api import async_playwright

async def main():
    resultados = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("1. Abriendo Google...")
        await page.goto("https://www.google.com", wait_until="domcontentloaded")

        print("2. Buscando: opticas Chile...")
        await page.locator("textarea[name='q'], input[name='q']").first.fill("ópticas Chile")
        await page.keyboard.press("Enter")

        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)

        print("3. Extrayendo resultados...")

        links = await page.locator("a").all()

        vistos = set()

        for link in links:
            try:
                texto = (await link.inner_text()).strip()
                href = await link.get_attribute("href")

                if not texto or not href:
                    continue

                if href.startswith("/"):
                    href = urljoin("https://www.google.com", href)

                if not href.startswith("http"):
                    continue

                if "google.com" in href:
                    continue

                if href in vistos:
                    continue

                vistos.add(href)

                resultados.append({
                    "nombre": texto[:200],
                    "url": href,
                    "productos": []
                })

                if len(resultados) >= 10:
                    break

            except Exception:
                continue

        datos = {
            "busqueda": "ópticas Chile",
            "total_resultados": len(resultados),
            "resultados": resultados
        }

        with open("investigacion.json", "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)

        print("")
        print("================================")
        print("INVESTIGACIÓN TERMINADA")
        print("================================")
        print(f"Resultados encontrados: {len(resultados)}")
        print("Archivo creado: investigacion.json")
        print("")

        for i, resultado in enumerate(resultados, 1):
            print(f"{i}. {resultado['nombre']}")
            print(f"   {resultado['url']}")

        await page.wait_for_timeout(3000)
        await browser.close()

asyncio.run(main())
