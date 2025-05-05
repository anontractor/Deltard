
from logger import logger

import asyncio
from playwright.async_api import async_playwright
from handlers import get_handler_for_page
from handlers.deltek import handle_timesheet_page
from utils import load_config

CONFIG_PATH = "config.yaml"

async def main():
    config = load_config(CONFIG_PATH)
    start_url = config.get("TIMECARD_SITE")

    if not start_url:
        raise ValueError("Missing 'TIMECARD_SITE' in configuration.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        logger.info(f"Navigating to {start_url}...")
        await page.goto(start_url)
        await page.wait_for_load_state("domcontentloaded")  # Optional: wait for base DOM
        await asyncio.sleep(2)
        # await page.wait_for_timeout(2000)  # fallback sleep

        while True:
            await page.wait_for_load_state("domcontentloaded")  # Optional: wait for base DOM
            await asyncio.sleep(2)
            handler = await get_handler_for_page(page)
            if handler:
                try:
                    await handler(page, config)
                    if handler == handle_timesheet_page:
                        logger.info('Done')
                        break
                except Exception as e:
                    logger.exception('Error on page.')
                    logger.exception(e)

            else:
                logger.warning("No handler matched the current page.")
                break

        # await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
