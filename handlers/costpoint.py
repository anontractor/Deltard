
from logger import logger


async def is_username_input_visible(page):
    try:
        input_box = await page.query_selector("input#USER")
        return await input_box.is_visible() if input_box else False
    except:
        return False

async def is_database_prompt_visible(page):
    try:
        input_box = await page.query_selector("input#DATABASE")
        return await input_box.is_visible() if input_box else False
    except:
        return False

async def is_session_warning_visible(page):
    try:
        warning = await page.query_selector("#warnMsgText")
        if warning:
            text = await warning.inner_text()
            return "are you sure you want to continue" in text.lower()
        return False
    except:
        return False


async def handle_session_warning(page, config):
    logger.info("Session warning detected. Clicking 'Ok' to proceed...")

    try:
        ok_button = await page.query_selector("#warnOkBtn")
        if ok_button:
            await ok_button.click()
        else:
            logger.info("Warning page found, but '#warnOkBtn' not located.")
    except Exception as e:
        logger.exception(f"Error while handling session warning: %s", e)


async def handle_database_prompt(page, config):
    logger.info("Database prompt detected. Filling and submitting...")

    try:
        input_box = await page.query_selector("input#DATABASE")
        if input_box:
            await input_box.fill(config["TIMECARD_CONFIG"])
        else:
            logger.info("DATABASE input box not found.")
            return

        login_button = await page.query_selector("input#btnPromptSSO")
        if login_button:
            await login_button.click()
        else:
            logger.warning("Login button not found.")
    except Exception as e:
        logger.exception(f"Error while handling database prompt: %s", e)


async def handle_username_input(page, config):
    logger.info("Username input detected. Filling in username and submitting...")

    try:
        input_box = await page.query_selector("input#USER")
        if input_box:
            await input_box.fill(config["LOGIN_USERNAME"])
            await input_box.press("Enter")
        else:
            logger.warning("Username input field not found.")
    except Exception as e:
        logger.exception(f"Error while handling username input: %s", e)
