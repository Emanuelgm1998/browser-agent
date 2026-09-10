import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("https://www.google.com")
        print("TITULO:", await page.title())
        print("URL:", page.url)
        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
