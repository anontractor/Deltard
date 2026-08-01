"""Unanet timesheet navigation and filling.

Grid layout (see docs/flow_map.md): table#timesheet with per-row inputs
project_name_rN / paycode_name_rN and day cells d_rN_C, where column C is the
0-based day offset from the period start date (parsed from the page title).
"""

import re
import time
from datetime import date, datetime, timedelta

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from logger import logger

PERIOD_RE = re.compile(r"\((\d{1,2}/\d{1,2}/\d{4}) - (\d{1,2}/\d{1,2}/\d{4})\)")
GRID = "table#timesheet"


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%m/%d/%Y").date()


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _grid_present(page: Page) -> bool:
    return page.locator(GRID).count() > 0


def open_current_timesheet(page: Page, config: dict):
    """Land on the editable grid for the current period, creating the
    timesheet first if the period doesn't have one yet."""
    base = config["UNANET_BASE"].rstrip("/")

    page.goto(f"{base}/action/time/current")
    page.wait_for_load_state("domcontentloaded")
    if _grid_present(page):
        return

    logger.info("No timesheet for the current period — creating one.")
    page.goto(f"{base}/action/time/add")
    page.wait_for_load_state("domcontentloaded")
    page.click("#button_save")  # date field is pre-populated with today
    page.wait_for_load_state("domcontentloaded")
    if _grid_present(page):
        return

    page.goto(f"{base}/action/time/current")
    page.wait_for_load_state("domcontentloaded")
    if not _grid_present(page):
        raise RuntimeError("Could not reach the timesheet grid after creating a timesheet.")


def _project_rows(page: Page) -> dict[int, str]:
    rows = {}
    for inp in page.locator('input[name^="project_name_r"]').all():
        idx = int(inp.get_attribute("name").rsplit("_r", 1)[1])
        rows[idx] = (inp.input_value() or "").strip()
    return rows


def _cell_value(page: Page, row: int, col: int) -> str:
    loc = page.locator(f'input[name="d_r{row}_{col}"]')
    if not loc.count():
        return ""
    return (loc.first.input_value() or "").strip()


def fill_timesheet(page: Page, config: dict) -> bool:
    """Fill HOURS_PER_DAY into the target project row for every empty,
    uncovered weekday from the period start through today. Returns True if
    anything was changed and saved."""
    hours = str(config.get("HOURS_PER_DAY", "8"))
    holidays = {_as_date(d) for d in (config.get("HOLIDAYS") or [])}

    match = PERIOD_RE.search(page.title())
    if not match:
        raise RuntimeError(f"Could not parse the period from page title: {page.title()!r}")
    period_start, period_end = _parse_date(match.group(1)), _parse_date(match.group(2))
    today = date.today()
    logger.info("Timesheet period %s - %s.", period_start, period_end)

    rows = _project_rows(page)
    target_project = config["UNANET_PROJECT"]
    matches = [idx for idx, name in rows.items() if name.startswith(target_project)]
    if not matches:
        raise RuntimeError(
            f"No row matches project prefix {target_project!r}. "
            f"Rows found: {[name for name in rows.values() if name]}"
        )
    target = matches[0]

    expected_paycode = (config.get("UNANET_PAYCODE") or "").strip()
    if expected_paycode:
        actual = (page.locator(f'input[name="paycode_name_r{target}"]').input_value() or "").strip()
        if actual != expected_paycode:
            raise RuntimeError(
                f"Row {target} paycode is {actual!r}, expected {expected_paycode!r} — refusing to fill."
            )

    filled = []  # (col, day)
    for col in range((min(today, period_end) - period_start).days + 1):
        day = period_start + timedelta(days=col)
        if day.weekday() >= 5 or day in holidays:
            continue
        # Skip days already covered by ANY row (e.g. leave rows).
        if any(_cell_value(page, idx, col) for idx in rows):
            continue
        logger.info("Filling %s hours for %s (d_r%s_%s).", hours, day, target, col)
        page.fill(f'input[name="d_r{target}_{col}"]', hours)
        filled.append((col, day))

    if not filled:
        logger.info("Nothing to fill — timesheet already up to date.")
        return False

    logger.info("Saving timesheet (%s day(s) filled).", len(filled))
    page.locator("#button_save").click()
    page.wait_for_load_state("domcontentloaded")

    try:
        page.locator(GRID).first.wait_for(state="visible", timeout=30_000)
    except PWTimeoutError:
        raise RuntimeError("Timesheet grid not visible after save — manual check needed.")
    time.sleep(1)  # let the reloaded grid populate its input values
    for col, day in filled:
        if not _cell_value(page, target, col):
            raise RuntimeError(f"Cell for {day} is empty after save — manual check needed.")

    logger.info("Saved: %s", ", ".join(str(day) for _, day in filled))
    return True
