
from playwright.async_api import Page

from logger import logger


async def is_trust_prompt_visible(page):
    try:
        title = await page.query_selector("#appConfirmTitle")
        if title and await title.is_visible():
            text = await title.inner_text()
            return "do you trust" in text.lower()
        return False
    except:
        return False


async def is_stay_signed_in_prompt(page: Page) -> bool:
    return await page.locator("div.row.text-title", has_text="Stay signed in?").is_visible()


async def handle_stay_signed_in_prompt(page: Page, config: dict):
    logger.info("Detected 'Stay signed in?' prompt. Clicking Yes...")
    await page.click("input#idSIButton9")
    await page.wait_for_load_state("domcontentloaded")


async def handle_trust_prompt(page, config):
    logger.info("Trust prompt detected. Clicking 'Continue'...")

    try:
        button = await page.query_selector("#idSIButton9")
        if button and await button.is_visible():
            await button.click()
        else:
            logger.warning("'Continue' button not found or not visible.")
    except Exception as e:
        logger.exception(f"Error while handling trust prompt: %s", e)
