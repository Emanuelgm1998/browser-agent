import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    with open("fuentes_opticas.json", "r", encoding="utf-8") as f:
        fuentes = json.load(f)

    resultados = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        for i, fuente in enumerate(fuentes, 1):
            url = fuente["url"]

            print()
            print("=" * 60)
            print(f"[{i}/{len(fuentes)}] Visitando: {url}")
            print("=" * 60)

            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000
                )

                await page.wait_for_timeout(3000)

                titulo = await page.title()

                texto = await page.locator("body").inner_text(
                    timeout=10000
                )

                resultados.append({
                    "url": url,
                    "titulo": titulo,
                    "texto": texto[:15000]
                })

                print("OK:", titulo)
                print("Caracteres:", len(texto))

            except Exception as e:
                print("ERROR:", str(e))
                resultados.append({
                    "url": url,
                    "error": str(e)
                })

        await browser.close()

    with open(
        "investigacion_opticas.json",
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
    print("=" * 60)
    print("INVESTIGACIÓN DIRECTA TERMINADA")
    print("=" * 60)
    print("Fuentes procesadas:", len(resultados))
    print("Archivo: investigacion_opticas.json")

asyncio.run(main())
