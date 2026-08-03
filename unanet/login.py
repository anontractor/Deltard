"""State machine for the Unanet login gauntlet.

Flow (mapped 2026-08-01, see docs/flow_map.md):
    unanet.biz -> Microsoft sign-in (email)
               -> corporate Okta widget (password, then RSA SecurID)
               -> Microsoft "Stay signed in?"
               -> unanet.biz

With a persistent browser profile most runs skip some or all of these states,
so every handler is optional and the loop simply exits once we land back on
Unanet.

Pacing note: handlers wait on element state rather than sleeping a fixed
amount. The Okta widget swaps factors in place without a navigation, so a
fill() against a node captured before the swap silently targets a stale
input — every interaction re-resolves its locator and waits for the widget to
settle first.
"""

import time
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from get_token_code import get_token_code
from logger import logger

MAX_TOKEN_ATTEMPTS = 3
TOKEN_ROTATION_WAIT_S = 35
ELEMENT_TIMEOUT_MS = 30_000
SETTLE_S = 1.5        # let post-render JS handlers bind before interacting
# RSA validation round-trips to an on-prem Authentication Manager before the
# WS-Fed redirect, and has been observed taking >25s. Too short a wait here
# reads a slow success as a failure.
TRANSITION_TIMEOUT_S = 90
UNKNOWN_PAGE_TIMEOUT_S = 60  # give up on an unrecognised page rather than spin

TRUST_TITLE = "#appConfirmTitle"
OKTA_FORM = 'form[data-se="o-form"]'
OKTA_PASSCODE = 'input[name="credentials.passcode"]'
OKTA_SUBMIT = 'input[type="submit"]'
OKTA_HEADING = 'h2[data-se="o-form-head"]'
OKTA_ERROR = 'div[data-se="o-form-error-container"]'


def _visible(page: Page, selector: str) -> bool:
    try:
        loc = page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _wait_visible(page: Page, selector: str, timeout_ms: int = ELEMENT_TIMEOUT_MS):
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout_ms)
    return loc


def _okta_heading(page: Page) -> str:
    try:
        loc = page.locator(OKTA_HEADING)
        if loc.count() and loc.first.is_visible():
            return loc.first.inner_text().strip().lower()
    except Exception:
        pass
    return ""


def _okta_error(page: Page) -> bool:
    try:
        loc = page.locator(OKTA_ERROR)
        return loc.count() > 0 and bool(loc.first.inner_text().strip())
    except Exception:
        return False


def _on_okta(page: Page) -> bool:
    """Still on the Okta widget? Keyed on the widget's own markup rather than a
    hostname, since the IdP domain is tenant-specific."""
    try:
        return page.locator(OKTA_FORM).count() > 0
    except Exception:
        return False


def _submit_okta_passcode(page: Page, value: str) -> str:
    """Fill and submit the Okta passcode field, then wait for the widget to
    respond. Returns "rejected", "advanced", or "stalled"."""
    heading_before = _okta_heading(page)

    _wait_visible(page, OKTA_PASSCODE).fill(value)
    _wait_visible(page, OKTA_SUBMIT).click()

    deadline = time.time() + TRANSITION_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(0.25)
        if not _on_okta(page):
            return "advanced"
        if _okta_error(page):
            return "rejected"
        if _okta_heading(page) not in (heading_before, ""):
            return "advanced"
    return "stalled"


def _handle_ms_email(page: Page, config: dict):
    logger.info("Filling username.")  # not logged: the log is shareable, it isn't
    _wait_visible(page, 'input[name="loginfmt"]').fill(config["LOGIN_USERNAME"])
    _wait_visible(page, "#idSIButton9").click()
    page.wait_for_load_state("domcontentloaded")


def _handle_okta_password(page: Page, config: dict):
    logger.info("Filling password.")
    result = _submit_okta_passcode(page, config["LOGIN_PASSWORD"])
    if result == "rejected":
        # Never retry a rejected password: repeated auth failures are the
        # signal most likely to lock the account.
        raise RuntimeError("Okta rejected the password — check secrets.env.")
    if result == "stalled":
        raise RuntimeError("Okta did not respond to the password submission.")


def _handle_okta_rsa(page: Page, config: dict):
    prefix = config.get("TOKEN_PREFIX") or ""
    postfix = config.get("TOKEN_POSTFIX") or ""

    for attempt in range(1, MAX_TOKEN_ATTEMPTS + 1):
        try:
            code = get_token_code()
        except Exception as exc:
            logger.exception("Token read failed (attempt %s): %s", attempt, exc)
            time.sleep(5)
            continue

        logger.info("Submitting RSA passcode (attempt %s).", attempt)
        result = _submit_okta_passcode(page, f"{prefix}{code}{postfix}")
        if result == "advanced":
            return
        if result == "stalled":
            # Don't call it a failure — the submission may still be in flight.
            # Hand back to the main loop, which re-detects the page; if we're
            # still on the RSA prompt it will land here again, and the overall
            # login budget bounds how many passcodes we can burn.
            logger.warning("No response %ss after submitting; letting the main "
                           "loop re-check the page.", TRANSITION_TIMEOUT_S)
            return

        logger.warning("RSA passcode rejected; waiting for the token to rotate...")
        time.sleep(TOKEN_ROTATION_WAIT_S)

    raise RuntimeError(f"RSA passcode not accepted after {MAX_TOKEN_ATTEMPTS} attempts.")


def _handle_kmsi(page: Page, config: dict):
    logger.info("'Stay signed in?' prompt — clicking Yes.")
    _wait_visible(page, "#idSIButton9").click()
    page.wait_for_load_state("domcontentloaded")


def _handle_trust_prompt(page: Page, config: dict):
    """Microsoft's "Do you trust <domain>?" account confirmation."""
    logger.info("'Do you trust' confirmation — clicking Continue.")
    # This page has a Cancel button too, and clicking it aborts the sign-in —
    # so match Continue by its accessible name and only fall back to the
    # primary-button id, never to "whatever button is first".
    button = page.get_by_role("button", name="Continue")
    if not button.count():
        button = page.locator("#idSIButton9")
    button.first.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
    button.first.click()
    page.wait_for_load_state("domcontentloaded")


def _describe_page(page: Page) -> str:
    """Compact description of an unrecognised page, for the log. Whatever this
    prints is what you need to write the next detector, so keep it specific."""
    try:
        bits = [f"url={page.url}", f"title={page.title()!r}"]
        headings = page.locator("h1, h2, [role='heading']")
        texts = []
        for i in range(min(headings.count(), 4)):
            text = (headings.nth(i).inner_text() or "").strip().replace("\n", " ")
            if text:
                texts.append(text[:60])
        if texts:
            bits.append("headings=[" + " | ".join(texts) + "]")

        buttons = page.locator(
            "button:visible, input[type=submit]:visible, input[type=button]:visible"
        )
        labels = []
        for i in range(min(buttons.count(), 6)):
            el = buttons.nth(i)
            label = ((el.inner_text() or "") or (el.get_attribute("value") or "")).strip()
            el_id = el.get_attribute("id")
            labels.append(f"{label[:24]!r}" + (f"#{el_id}" if el_id else ""))
        if labels:
            bits.append("buttons=[" + ", ".join(labels) + "]")
        return "; ".join(bits)
    except Exception as exc:
        return f"(could not describe page: {type(exc).__name__})"


def _detect(page: Page) -> str | None:
    # Order matters: the MS email page also contains #idSIButton9.
    if _visible(page, 'input[name="loginfmt"]'):
        return "ms_email"

    heading = _okta_heading(page)
    if "password" in heading:
        return "okta_password"
    if "securid" in heading:
        return "okta_rsa"

    # "Do you trust <domain>?" — appears after federated sign-in, typically on a
    # profile Microsoft doesn't recognise yet. Shares #idSIButton9 with the KMSI
    # page, so key on its own heading; the two are otherwise disjoint.
    try:
        title = page.locator(TRUST_TITLE)
        if title.count() and title.first.is_visible():
            if "do you trust" in title.first.inner_text().strip().lower():
                return "trust_prompt"
    except Exception:
        pass

    try:
        if page.get_by_text("Stay signed in?").count() and _visible(page, "#idSIButton9"):
            return "kmsi"
    except Exception:
        pass
    return None


_HANDLERS = {
    "ms_email": _handle_ms_email,
    "okta_password": _handle_okta_password,
    "okta_rsa": _handle_okta_rsa,
    "trust_prompt": _handle_trust_prompt,
    "kmsi": _handle_kmsi,
}


def login(page: Page, config: dict, timeout_s: int = 300):
    """Drive the login flow until we land on Unanet. Idempotent per state."""
    deadline = time.time() + timeout_s
    last_state = None
    unknown_since = None
    unknown_url = None

    while time.time() < deadline:
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            page.wait_for_load_state("domcontentloaded")
        time.sleep(SETTLE_S)

        host = urlparse(page.url).hostname or ""
        if host.endswith("unanet.biz"):
            logger.info("Login complete: %s", page.url)
            return

        state = _detect(page)
        if state:
            if state != last_state:
                logger.info("Login state: %s", state)
            _HANDLERS[state](page, config)
            unknown_since = unknown_url = None
        else:
            # An unrecognised page used to spin here silently until the overall
            # timeout, leaving only a screenshot. Describe it once per URL, then
            # give up quickly — the message carries what a new detector needs.
            if page.url != unknown_url:
                unknown_url = page.url
                logger.warning("No handler for this page — %s", _describe_page(page))
            if unknown_since is None:
                unknown_since = time.time()
            elif time.time() - unknown_since > UNKNOWN_PAGE_TIMEOUT_S:
                raise RuntimeError(f"Stuck on an unhandled page: {_describe_page(page)}")
        last_state = state

    raise TimeoutError(
        f"Login did not complete within {timeout_s} seconds. Last page: {_describe_page(page)}"
    )
