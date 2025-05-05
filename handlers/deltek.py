from __future__ import annotations

import asyncio
from datetime import datetime, date
import re
from typing import List, Union

from bs4 import BeautifulSoup

from logger import logger


DAY_PATTERN = re.compile(r"^DAY\d{1,2}_")


async def is_timesheet_page(page):
    await asyncio.sleep(5)
    try:
        label = await page.query_selector("div.gBxSIBHdrLabel")
        if label:
            text = await label.inner_text()
            return text.strip().lower() == "timesheet"
        return False
    except:
        return False


def parse_datetime_from_label(label: str) -> date | None:
    try:
        return datetime.strptime(label.strip(), "%m/%d/%y").date()
    except ValueError:
        return None


def parse_table_headers(tHdr_html: str) -> List[Union[str, date]]:
    soup = BeautifulSoup(tHdr_html, "html.parser")
    headers = []

    for div in soup.select("div.hdcll"):
        hd_txt = div.select_one("div.hdTxt")
        if not hd_txt:
            headers.append("")  # keep placeholder for position
            continue

        parts = hd_txt.select("div.hdDiv")
        texts = [part.get_text(strip=True) for part in parts if part.get_text(strip=True)]

        if texts:
            maybe_date = parse_datetime_from_label(texts[-1])
            if maybe_date:
                headers.append(maybe_date)
            else:
                headers.append(" ".join(texts))
        else:
            headers.append("")

    return headers


async def extract_visible_inputs_from_page(page):
    rows = []
    row_locators = await page.locator("div.dRw").all()

    for row in row_locators:
        row_cells = []
        input_elements = await row.locator("input").all()

        for input_el in input_elements:
            try:
                parent = await input_el.evaluate_handle("el => el.parentElement")
                is_visible = await parent.evaluate("""
                    el => {
                        const style = window.getComputedStyle(el);
                        return style && style.display !== "none" && style.visibility !== "hidden";
                    }
                """)
                if not is_visible:
                    continue

                value = await input_el.evaluate("el => el.value")
                row_cells.append((input_el, value if value else ""))
            except Exception as e:
                logger.exception(f"Error evaluating input: {e}")

        if row_cells:
            rows.append(row_cells)

    return rows


async def get_visible_right_scroll_button(page):
    matches = await page.locator("#hp2").all()
    for btn in matches:
        if await btn.is_visible():
            return btn
    return None


async def handle_timesheet_page(page, config):
    project_id, pay_type = config['DELTEK_PROJECT_ID'], config['DELTEK_PAY_TYPE']
    today = date.today()

    async def get_headers():
        html = await page.locator("#tHdr").inner_html()
        return parse_table_headers(html)

    async def get_rows(headers):
        return [
            row for row in await extract_visible_inputs_from_page(page)
            if len(row) == len(headers)
        ]

    max_scroll_attempts = 20
    scrolls_done = 0

    is_updated = False
    while scrolls_done < max_scroll_attempts:
        logger.info(f"Scroll window %s...", scrolls_done + 1)

        headers = await get_headers()
        if not headers:
            logger.warning("No headers found after scroll.")
            break

        fillable_indices = [
            idx for idx, header in enumerate(headers)
            if isinstance(header, date)
            and header <= today
            and header.weekday() < 5
        ]

        if not fillable_indices:
            logger.info("No fillable date columns in this scroll window.")
        else:
            rows = await get_rows(headers)
            if not rows:
                logger.warning("No rows found after scroll.")
                break

            dummy_input_el, _ = rows[0][0]

            for idx in fillable_indices:
                input_el, value = rows[0][idx]
                if not value.strip():
                    logger.info(f"Filling row 0, column %s with '8'", idx)
                    await input_el.fill("8")
                    await dummy_input_el.click()
                    is_updated = True
                    await asyncio.sleep(2)

        # Try scrolling 3 clicks to the right
        right_button = await get_visible_right_scroll_button(page)
        if right_button and await right_button.is_visible():
            logger.info("Scrolling right...")
            for _ in range(3):
                await right_button.click()
                await asyncio.sleep(0.4)
            scrolls_done += 1
        else:
            logger.warning("No more visible right scroll button.")
            break

    if not is_updated:
        logger.info('No changes made.')
        return

    # Final blur/save after all scrolls
    headers = await get_headers()
    rows = await get_rows(headers)
    if rows:
        dummy_input_el, _ = rows[0][0]
        await dummy_input_el.click()

    # Final save
    save_button = page.locator("#svCntBttn")
    if await save_button.is_visible():
        logger.info("Clicking 'Save & Continue'...")
        await save_button.click()  # Uncomment when ready
    else:
        raise ValueError("Save button not found.")

    await asyncio.sleep(2)


