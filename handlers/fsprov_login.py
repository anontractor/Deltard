
import asyncio
from playwright.async_api import Page
from get_token_code import get_token_code

from logger import logger


async def is_username_prompt_visible(page: Page) -> bool:
    return await page.locator("input#userNameInput").is_visible()


async def is_password_prompt_visible(page: Page) -> bool:
    return await page.locator("input#passwordInput").is_visible()


async def is_token_prompt_visible(page: Page) -> bool:
    return await page.locator("input#passcodeInput").is_visible()


async def handle_username_prompt(page: Page, config: dict):
    username = config.get("LOGIN_USERNAME")
    if not username:
        raise ValueError("Missing LOGIN_USERNAME in config.")

    logger.info(f"Filling username: %s", username)
    await page.fill("input#userNameInput", username)
    await page.press("input#userNameInput", "Enter")  # triggers the JS event
    await page.wait_for_load_state("domcontentloaded")  # wait for page update


async def handle_password_prompt(page: Page, config: dict):
    password = config.get("LOGIN_PASSWORD")
    if not password:
        raise ValueError("Missing LOGIN_PASSWORD in config.")

    logger.info("Filling password.")
    await page.fill("input#passwordInput", password)
    await page.press("input#passwordInput", "Enter")  # triggers the next step
    await page.wait_for_load_state("domcontentloaded")


async def handle_token_prompt(page: Page, config: dict):
    prefix = config.get("TOKEN_PREFIX", "")
    postfix = config.get("TOKEN_POSTFIX", "")

    for attempt in range(3):  # Max 3 tries
        try:
            token = get_token_code()
        except ValueError as e:
            logger.exception(e)
            if attempt < 2:
                logger.info('Trying again....')
            continue

        passcode = f"{prefix}{token}{postfix}"
        logger.info(f"Attempt %s: submitting passcode %s", attempt + 1, passcode)

        await page.fill("input#passcodeInput", passcode)
        await page.press("input#passcodeInput", "Enter")
        await page.wait_for_load_state("domcontentloaded")

        # Wait briefly to see if error appears
        await asyncio.sleep(2)

        error_visible = await page.locator("label#errorText").is_visible()
        retry_button = page.locator("button#btn_retry_verification")
        retry_visible = await retry_button.is_visible()

        if not error_visible and not retry_visible:
            logger.info("Passcode accepted.")
            return

        logger.info("Passcode failed.")

        if retry_visible:
            logger.info("Clicking retry button...")
            await retry_button.click()

        logger.info("Waiting 30 seconds before retrying...")
        await asyncio.sleep(30)

    raise RuntimeError("Failed to submit valid token code after 3 attempts.")
