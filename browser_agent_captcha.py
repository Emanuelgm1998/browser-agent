import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = Path("browser_profile")
DEFAULT_OUTPUT = Path("google_resultados.json")


async def search_google_persistent(
    query: str,
    profile_dir: Path = PROFILE,
    headless: bool = False,
    save_to: Path | None = None,
) -> list[dict]:
    """
    Busca en Google usando un contexto persistente (mantiene sesion/cookies
    entre ejecuciones). Detecta bloqueo/CAPTCHA via URL y pausa para
    resolucion manual.

    Esta funcion NO se ejecuta automaticamente al importar el modulo.
    NO escribe a disco a menos que se pase explicitamente 'save_to'.

    NOTA: existe superposicion funcional con browser_agent_core.search_google().
    Diferencia clave: esa usa sesion nueva cada vez (launch), esta usa
    sesion persistente (launch_persistent_context) + deteccion de CAPTCHA.
    Pendiente de decidir si se consolidan en un solo modulo.
    """

    async with async_playwright() as p:

        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            viewport={"width": 1366, "height": 900},
        )

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            print("Abriendo Google...")
            await page.goto(
                "https://www.google.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )

            await page.wait_for_timeout(2000)

            # Deteccion de bloqueo/CAPTCHA
            if "/sorry/" in page.url or "captcha" in page.url.lower():
                print()
                print("========================================")
                print(" CAPTCHA / BLOQUEO DETECTADO")
                print(" Resuelvelo manualmente en Chromium.")
                print(" NO cierres la ventana.")
                print("========================================")
                input("Cuando termines, presiona ENTER aqui...")

            print("URL actual:", page.url)

            if "/sorry/" in page.url:
                print("Google sigue bloqueando esta sesion.")
                print("Abortando este intento.")
                return []

            buscador = page.locator(
                'textarea[name="q"], input[name="q"]'
            ).first

            await buscador.wait_for(timeout=10000)
            await buscador.fill(query)
            await buscador.press("Enter")

            await page.wait_for_timeout(4000)

            print("URL despues de buscar:", page.url)

            if "/sorry/" in page.url:
                print()
                print("Google volvio a solicitar verificacion.")
                print("Resuelvela manualmente y presiona ENTER.")
                input()

            resultados: list[dict] = []

            for enlace in await page.locator("a").all():
                try:
                    texto = (await enlace.inner_text()).strip()
                    url = await enlace.get_attribute("href")

                    if texto and url and url.startswith("http"):
                        resultados.append({
                            "texto": texto,
                            "url": url,
                        })
                except Exception:
                    continue

            if save_to is not None:
                save_to.write_text(
                    json.dumps(
                        resultados,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print("Archivo creado:", save_to)

            print()
            print("========================================")
            print(" INVESTIGACION TERMINADA")
            print(" Resultados:", len(resultados))
            print("========================================")

            return resultados

        finally:
            await context.close()


async def main() -> None:
    """
    Prueba manual del modulo. Aqui si guardamos a disco explicitamente.
    """
    await search_google_persistent(
        query="opticas Chile",
        save_to=DEFAULT_OUTPUT,
    )


if __name__ == "__main__":
    asyncio.run(main())
