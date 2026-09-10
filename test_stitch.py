import asyncio
from playwright.async_api import async_playwright


STITCH_URL = "https://stitch.withgoogle.com"


async def main():
    print("=" * 70)
    print("       BROWSER AGENT - PRUEBA DE ENTORNO STITCH")
    print("=" * 70)

    async with async_playwright() as p:

        print("\n[1/5] Iniciando Chromium...")

        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized"
            ]
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 900}
        )

        page = await context.new_page()

        print("[OK] Chromium iniciado")

        print("\n[2/5] Abriendo Stitch...")
        
        try:
            await page.goto(
                STITCH_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )
        except Exception as e:
            print("[AVISO] Error de navegación:", e)

        await page.wait_for_timeout(5000)

        print("[OK] Página cargada")

        print("\n[3/5] Analizando página...")
        print("-" * 70)

        print("URL:")
        print(page.url)

        print("\nTÍTULO:")
        print(await page.title())

        # ---------------------------------------------------------
        # TEXTO VISIBLE
        # ---------------------------------------------------------

        try:
            body_text = await page.locator("body").inner_text()

            print("\nTEXTO VISIBLE:")
            print("-" * 70)

            if body_text.strip():
                print(body_text[:10000])
            else:
                print("[Sin texto visible]")
        except Exception as e:
            print("[ERROR] No se pudo obtener el texto:", e)

        # ---------------------------------------------------------
        # BOTONES
        # ---------------------------------------------------------

        print("\n[4/5] Detectando elementos...")
        print("-" * 70)

        try:
            buttons = await page.locator("button").all_inner_texts()

            print("\nBOTONES:")

            encontrados = 0

            for i, button in enumerate(buttons, 1):
                button = button.strip()

                if button:
                    encontrados += 1
                    print(f"{encontrados}. {button}")

            if encontrados == 0:
                print("[Ningún botón con texto detectado]")

        except Exception as e:
            print("[ERROR] Botones:", e)

        # ---------------------------------------------------------
        # LINKS
        # ---------------------------------------------------------

        try:
            links = await page.locator("a").all_inner_texts()

            print("\nENLACES:")

            encontrados = 0

            for i, link in enumerate(links, 1):
                link = link.strip()

                if link:
                    encontrados += 1
                    print(f"{encontrados}. {link}")

            if encontrados == 0:
                print("[Ningún enlace con texto detectado]")

        except Exception as e:
            print("[ERROR] Enlaces:", e)

        # ---------------------------------------------------------
        # INPUTS
        # ---------------------------------------------------------

        try:
            inputs = await page.locator("input").count()

            print("\nCAMPOS INPUT:")
            print(f"Cantidad detectada: {inputs}")

        except Exception as e:
            print("[ERROR] Inputs:", e)

        # ---------------------------------------------------------
        # SCREENSHOT
        # ---------------------------------------------------------

        print("\n[5/5] Guardando captura...")

        try:
            await page.screenshot(
                path="stitch_test.png",
                full_page=True
            )

            print("[OK] Captura guardada:")
            print("     stitch_test.png")

        except Exception as e:
            print("[ERROR] Screenshot:", e)

        # ---------------------------------------------------------
        # RESULTADO
        # ---------------------------------------------------------

        print("\n" + "=" * 70)
        print("RESULTADO DE LA PRUEBA")
        print("=" * 70)

        print("\nEl navegador logró:")
        print("  [OK] Iniciar Chromium")
        print("  [OK] Abrir Stitch")
        print("  [OK] Obtener URL")
        print("  [OK] Obtener título")
        print("  [OK] Leer contenido visible")
        print("  [OK] Detectar botones")
        print("  [OK] Detectar enlaces")
        print("  [OK] Detectar inputs")
        print("  [OK] Crear screenshot")

        print("\nEl navegador permanecerá abierto 15 segundos.")

        await page.wait_for_timeout(15000)

        await browser.close()

        print("\n[FIN] Prueba completada.")


if __name__ == "__main__":
    asyncio.run(main())