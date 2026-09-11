import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_FILE = BASE_DIR / "google_resultados.json"


def _extraer_url_real(href: str) -> str:
    """
    Google envuelve los links de resultados en /url?q=...&sa=U&ved=...
    Esta funcion devuelve la URL de destino real, no el redirect.
    """
    if href.startswith("/url?"):
        qs = parse_qs(urlparse(href).query)
        return qs.get("q", [href])[0]
    return href


async def search_google(
    query: str,
    headless: bool = False,
    max_results: int = 50,
    save_to: Path | None = None,
) -> list[dict]:
    """
    Ejecuta una busqueda de Google y devuelve los enlaces encontrados.

    Esta funcion NO se ejecuta automaticamente al importar el modulo.
    NO escribe a disco a menos que se pase explicitamente 'save_to'.

    Nota de diseno: esta funcion asume el proveedor "google_scrape".
    Cuando se agregue soporte para otros proveedores (Bing, DuckDuckGo,
    Search API), extraer esta logica a un dispatcher tipo:
        search(query, provider="google_scrape" | "bing_scrape" | ...)
    manteniendo esta funcion como uno de los backends posibles.
    """

    resultados: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        try:
            page = await browser.new_page()

            print(f"Abriendo Google para: {query}")

            await page.goto(
                "https://www.google.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )

            print("Google abierto:", await page.title())

            buscador = page.locator(
                'textarea[name="q"], input[name="q"]'
            ).first

            await buscador.wait_for(timeout=10000)
            await buscador.fill(query)
            await buscador.press("Enter")

            try:
                await page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=15000,
                )
            except Exception:
                pass

            await page.wait_for_timeout(1500)

            print("URL:", page.url)
            print("Titulo:", await page.title())

            enlaces = await page.locator("a").all()

            for enlace in enlaces[:max_results]:
                try:
                    texto = (await enlace.inner_text()).strip()
                    href = await enlace.get_attribute("href")

                    if texto and href:
                        url_real = _extraer_url_real(href)

                        resultados.append(
                            {
                                "texto": texto,
                                "url": url_real,
                            }
                        )

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
            print("===== RESULTADO =====")
            print("Enlaces encontrados:", len(resultados))

            for resultado in resultados[:10]:
                print("-", resultado["texto"][:80])
                print(" ", resultado["url"])

            return resultados

        finally:
            await browser.close()


async def main() -> None:
    """
    Prueba manual del modulo. Aqui si guardamos a disco explicitamente.
    """
    await search_google(
        query="opticas Chile",
        headless=False,
        save_to=DEFAULT_RESULTS_FILE,
    )


if __name__ == "__main__":
    asyncio.run(main())
