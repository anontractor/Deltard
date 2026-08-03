"""Entry point for the Unanet timecard automation (part 2 rewrite).

Launches real installed Chrome with a persistent profile (./browser_profile)
so Microsoft sees the same known device on every run, logs in through the
MS -> Okta -> RSA flow, then fills and saves the current timesheet.

Usage:
    python run_unanet.py                     # normal scheduled run
    python run_unanet.py --force             # run even on a weekend
    python run_unanet.py --test-notify       # send a test notification
    python run_unanet.py --test-notify fail  # send a sample failure alert

Notification titles are deliberately generic: an ntfy topic is readable by
anyone who knows it, so the alert says what broke, not whose timecard it was.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from logger import logger
from unanet.config import load_config
from unanet.login import login
from unanet.notify import notify, ping_healthcheck
from unanet.timesheet import fill_timesheet, open_current_timesheet

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / "browser_profile"
FAILURE_SCREENSHOT = ROOT / "last_failure.png"

# Alerts carry no detail: an ntfy topic is readable by anyone who knows its
# name, and the diagnostics are all in automation.log anyway.
FAIL_TITLE = "Autofill automation FAILED"
FAIL_BODY = "Check the logs."
OK_TITLE = "Autofill automation OK"
OK_BODY = "Completed."
# The script never submits, so the last workday of the period needs a human.
FINAL_TITLE = "Autofill automation - action needed"  # ASCII only: see notify.py
FINAL_BODY = "Final workday of the period. Submit manually before the deadline."


def _run(config: dict) -> bool:
    base = config["UNANET_BASE"].rstrip("/")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            logger.info("Navigating to %s...", f"{base}/action/home")
            page.goto(f"{base}/action/home")
            login(page, config)
            open_current_timesheet(page, config)
            return fill_timesheet(page, config)
        except Exception:
            try:
                page.screenshot(path=str(FAILURE_SCREENSHOT), full_page=True)
                logger.error("Failure screenshot saved to %s", FAILURE_SCREENSHOT)
            except Exception:
                pass
            raise
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description="Fill today's Unanet timecard.")
    parser.add_argument("--force", action="store_true",
                        help="run even on a weekend (overrides RUN_ON_WEEKENDS)")
    parser.add_argument("--test-notify", nargs="?", const="ok",
                        choices=("ok", "fail", "final"),
                        help="send a test notification and exit "
                             "('fail' = sample failure, 'final' = submit reminder)")
    args = parser.parse_args()

    config = load_config(ROOT)

    if args.test_notify == "ok":
        notify(config, OK_TITLE, OK_BODY)
        return
    if args.test_notify == "fail":
        notify(config, FAIL_TITLE, FAIL_BODY, urgent=True)
        return
    if args.test_notify == "final":
        notify(config, FINAL_TITLE, FINAL_BODY, urgent=True)
        return

    # Bail before opening a browser: there's nothing to log on a weekend, and
    # the site is typically locked for labor processing anyway. Deliberately
    # silent — no alert, and no healthcheck ping (schedule the healthcheck for
    # weekdays only, so a quiet weekend isn't read as a missed run).
    if date.today().weekday() >= 5 and not (args.force or config.get("RUN_ON_WEEKENDS")):
        logger.info("Weekend — skipping (use --force to override).")
        return

    try:
        result = _run(config)
    except Exception:
        # Full diagnostics go to the log and the screenshot, not the alert.
        logger.exception("Run failed.")
        notify(config, FAIL_TITLE, FAIL_BODY, urgent=True)
        ping_healthcheck(config, ok=False)
        sys.exit(1)

    logger.info("Done. %s", "Filled and saved." if result.changed
                else "Already up to date; nothing to do.")

    settings = config.get("NOTIFY") or {}
    if result.is_final_weekday and settings.get("remind_on_final_weekday", True):
        # Supersedes the routine OK ping: this one needs to stand out.
        notify(config, FINAL_TITLE, FINAL_BODY, urgent=True)
    elif settings.get("notify_on_success"):
        notify(config, OK_TITLE, OK_BODY)
    ping_healthcheck(config, ok=True)


if __name__ == "__main__":
    main()
