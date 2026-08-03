"""Failure/success notifications for unattended runs.

Channels (pick one via NOTIFY.channel in config.yaml):
  ntfy   - HTTP POST to ntfy.sh; the phone app subscribes to a topic and
           gets a real push notification. No account needed.
  email  - SMTP; for Gmail this needs an app password, not the account
           password.
  none   - disabled.

Also supports an optional dead-man's-switch ping (healthchecks.io style):
the script pings on success, and the monitoring service alerts *you* if the
ping never arrives — which is the only way to catch the failure mode where
the script never runs at all.

Everything here is best-effort: a notification problem must never mask the
error it was trying to report.
"""

import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from logger import logger

TIMEOUT_S = 15
SECRET_KEYS = ("LOGIN_PASSWORD", "TOKEN_PREFIX", "TOKEN_POSTFIX",
               "NTFY_TOPIC", "SMTP_PASSWORD")


def _settings(config: dict) -> dict:
    return config.get("NOTIFY") or {}


def redact(text: str, config: dict) -> str:
    """Strip any configured secret that leaked into a message body."""
    for key in SECRET_KEYS:
        value = config.get(key)
        if value and len(str(value)) >= 4:
            text = text.replace(str(value), f"<{key}>")
    return text


def _send_ntfy(config: dict, title: str, body: str, urgent: bool):
    settings = _settings(config).get("ntfy") or {}
    topic = config.get("NTFY_TOPIC")
    if not topic:
        raise ValueError("NTFY_TOPIC is not set in secrets.env")

    server = (settings.get("server") or "https://ntfy.sh").rstrip("/")
    # ntfy takes the title as an HTTP header, and headers are latin-1 encoded —
    # a stray em dash or emoji in the title would raise from inside http.client.
    # The body is fine: it's UTF-8 encoded bytes.
    safe_title = title.encode("ascii", "replace").decode("ascii")
    request = urllib.request.Request(
        f"{server}/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": safe_title,
            "Priority": "high" if urgent else "low",
            "Tags": "rotating_light" if urgent else "white_check_mark",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        response.read()


def _send_email(config: dict, title: str, body: str, urgent: bool):
    settings = _settings(config).get("email") or {}
    user, password = config.get("SMTP_USER"), config.get("SMTP_PASSWORD")
    if not (user and password):
        raise ValueError("SMTP_USER / SMTP_PASSWORD are not set in secrets.env")

    message = EmailMessage()
    message["Subject"] = title
    message["From"] = user
    message["To"] = settings.get("to") or user
    message.set_content(body)

    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or 587)
    with smtplib.SMTP(host, port, timeout=TIMEOUT_S) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)


_CHANNELS = {"ntfy": _send_ntfy, "email": _send_email}


def notify(config: dict, title: str, body: str, urgent: bool = False):
    """Send a notification. Never raises."""
    channel = (_settings(config).get("channel") or "none").lower()
    if channel == "none":
        logger.info("Notifications disabled; would have sent: %s", title)
        return

    sender = _CHANNELS.get(channel)
    if sender is None:
        logger.error("Unknown NOTIFY.channel %r — no notification sent.", channel)
        return

    try:
        sender(config, title, redact(body, config), urgent)
        logger.info("Notification sent via %s: %s", channel, title)
    except Exception as exc:
        logger.error("Could not send %s notification (%s): %s",
                     channel, type(exc).__name__, exc)


def ping_healthcheck(config: dict, ok: bool = True):
    """Ping a healthchecks.io-style dead-man's switch. Never raises."""
    url = config.get("HEALTHCHECK_URL") or _settings(config).get("healthcheck_url")
    if not url:
        return
    target = url.rstrip("/") if ok else f"{url.rstrip('/')}/fail"
    try:
        with urllib.request.urlopen(target, timeout=TIMEOUT_S) as response:
            response.read()
        logger.info("Healthcheck pinged (ok=%s).", ok)
    except Exception as exc:
        logger.error("Healthcheck ping failed: %s", exc)
