"""Contact method integrations for AI Off-Ramp.

Handles actually sending messages via email, Telegram, SMS, or ntfy push.
Each method is async and returns a send result.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Any

import aiohttp

from .config import (
    Contact,
    EmailConfig,
    Integrations,
    NtfyConfig,
    SmsConfig,
    TelegramConfig,
)

logger = logging.getLogger("ai_off_ramp.contacts")


@dataclass
class SendResult:
    success: bool
    method: str
    contact_id: str
    contact_name: str
    error: str | None = None
    details: dict[str, Any] | None = None


async def send_email(
    config: EmailConfig,
    to_address: str,
    subject: str,
    body: str,
    contact: Contact,
) -> SendResult:
    """Send an email via SMTP."""
    try:
        import aiosmtplib

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"{config.from_name} <{config.from_address}>"
        msg["To"] = to_address
        msg["Subject"] = subject

        await aiosmtplib.send(
            msg,
            hostname=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_user,
            password=config.smtp_password,
            use_tls=False,
            start_tls=True,
        )
        logger.info(f"Email sent to {contact.name} ({contact.id}) at {to_address}")
        return SendResult(
            success=True,
            method="email",
            contact_id=contact.id,
            contact_name=contact.name,
        )
    except Exception as e:
        logger.error(f"Failed to send email to {contact.name}: {e}")
        return SendResult(
            success=False,
            method="email",
            contact_id=contact.id,
            contact_name=contact.name,
            error=str(e),
        )


def _telegram_payload(chat_id: str, body: str) -> dict[str, Any]:
    """Build the sendMessage payload as PLAIN TEXT.

    📨 No parse_mode, on purpose. This used to send parse_mode="HTML" with an
    unescaped body, so any "<" or "&" in the AI's context line (a blood
    pressure "<90", "Tom & Jo's place") could make Telegram reject the
    whole message as unparseable. Escalation messages are plain prose; plain
    text can't fail to parse.
    """
    return {"chat_id": chat_id, "text": body}


async def send_telegram(
    config: TelegramConfig,
    chat_id: str,
    body: str,
    contact: Contact,
) -> SendResult:
    """Send a Telegram message via Bot API."""
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = _telegram_payload(chat_id, body)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"Telegram sent to {contact.name} ({contact.id})")
                    return SendResult(
                        success=True,
                        method="telegram",
                        contact_id=contact.id,
                        contact_name=contact.name,
                    )
                else:
                    error = data.get("description", "Unknown Telegram error")
                    logger.error(f"Telegram failed for {contact.name}: {error}")
                    return SendResult(
                        success=False,
                        method="telegram",
                        contact_id=contact.id,
                        contact_name=contact.name,
                        error=error,
                    )
    except Exception as e:
        logger.error(f"Failed to send Telegram to {contact.name}: {e}")
        return SendResult(
            success=False,
            method="telegram",
            contact_id=contact.id,
            contact_name=contact.name,
            error=str(e),
        )


async def send_sms(
    config: SmsConfig,
    to_number: str,
    body: str,
    contact: Contact,
) -> SendResult:
    """Send an SMS via Twilio."""
    try:
        from twilio.rest import Client as TwilioClient

        client = TwilioClient(config.twilio_sid, config.twilio_token)
        message = client.messages.create(
            body=body,
            from_=config.twilio_from,
            to=to_number,
        )
        logger.info(f"SMS sent to {contact.name} ({contact.id}): SID {message.sid}")
        return SendResult(
            success=True,
            method="sms",
            contact_id=contact.id,
            contact_name=contact.name,
            details={"sid": message.sid},
        )
    except ImportError:
        return SendResult(
            success=False,
            method="sms",
            contact_id=contact.id,
            contact_name=contact.name,
            error="Twilio package not installed. Install with: pip install ai-off-ramp[sms]",
        )
    except Exception as e:
        logger.error(f"Failed to send SMS to {contact.name}: {e}")
        return SendResult(
            success=False,
            method="sms",
            contact_id=contact.id,
            contact_name=contact.name,
            error=str(e),
        )


# 📣 ntfy (https://ntfy.sh) — free push notifications, no account, no phone
# number. Priorities are ntfy's own 1-5 scale (docs.ntfy.sh/publish/#message-priority):
#   3 = default: short vibration and sound
#   4 = high:    long vibration burst, sound, pop-over
#   5 = max:     really long vibration bursts, sound, pop-over
# Whether a priority can break through Do Not Disturb is a per-priority
# setting the PERSON turns on in the ntfy Android app; we can't force it.
NTFY_TIER_PRIORITY: dict[str, int] = {
    "check_in": 3,
    "concerned": 4,
    "urgent": 4,
    "emergency": 5,
}

# Every Off-Ramp push also carries this plain-text tag, so anything else
# listening on the same topic (a notes inbox, a bot, a poller) can tell an
# Off-Ramp alert apart from a message the person typed themselves. Found the
# hard way: our own test pushes got filed as "notes from Ren" by a poller
# sharing the topic (2026-10-03).
NTFY_SOURCE_TAG = "ai-off-ramp"

# Tags that ntfy shows as emoji in front of the title.
NTFY_TIER_TAGS: dict[str, list[str]] = {
    "check_in": ["wave"],
    "concerned": ["warning"],
    "urgent": ["warning"],
    "emergency": ["rotating_light"],
}


def ntfy_priority_for(contact: Contact, tier: str | None) -> int:
    """Contact override wins (the self contact sets 5), else the tier's mapping."""
    if contact.ntfy_priority is not None:
        return contact.ntfy_priority
    return NTFY_TIER_PRIORITY.get(tier or "", 3)


def _ntfy_request(
    config: NtfyConfig,
    topic: str,
    subject: str,
    body: str,
    contact: Contact,
    tier: str | None,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Build (url, json_payload, headers) for one ntfy publish.

    We publish as JSON to the server ROOT, not with X-Title headers: titles
    carry people's names, and non-ASCII in HTTP headers is exactly where
    libraries garble things. JSON is plain UTF-8, no encoding tricks.

    A topic written as a full URL ("https://ntfy.example.com/alex-x7f...")
    uses that server for this one contact.
    """
    server = config.server
    if topic.startswith(("http://", "https://")):
        server, _, topic = topic.rstrip("/").rpartition("/")
    payload: dict[str, Any] = {
        "topic": topic,
        "title": subject,
        "message": body,
        "priority": ntfy_priority_for(contact, tier),
        "tags": NTFY_TIER_TAGS.get(tier or "", ["wave"]) + [NTFY_SOURCE_TAG],
    }
    headers: dict[str, str] = {}
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    elif config.username and config.password:
        raw = f"{config.username}:{config.password}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    return server.rstrip("/") + "/", payload, headers


async def send_ntfy(
    config: NtfyConfig,
    topic: str,
    subject: str,
    body: str,
    contact: Contact,
    tier: str | None = None,
) -> SendResult:
    """Publish a push notification via ntfy."""
    url, payload, headers = _ntfy_request(config, topic, subject, body, contact, tier)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if 200 <= resp.status < 300:
                    logger.info(f"ntfy sent to {contact.name} ({contact.id}), priority {payload['priority']}")
                    try:
                        message_id = (await resp.json(content_type=None)).get("id")
                    except Exception:
                        message_id = None
                    return SendResult(
                        success=True,
                        method="ntfy",
                        contact_id=contact.id,
                        contact_name=contact.name,
                        details={"priority": payload["priority"], "http_status": resp.status,
                                 "ntfy_message_id": message_id},
                    )
                text = (await resp.text())[:300]
                logger.error(f"ntfy failed for {contact.name}: HTTP {resp.status} {text}")
                return SendResult(
                    success=False,
                    method="ntfy",
                    contact_id=contact.id,
                    contact_name=contact.name,
                    error=f"HTTP {resp.status}: {text}",
                )
    except Exception as e:
        logger.error(f"Failed to send ntfy to {contact.name}: {e}")
        return SendResult(
            success=False,
            method="ntfy",
            contact_id=contact.id,
            contact_name=contact.name,
            error=str(e) or type(e).__name__,
        )


async def send_message(
    integrations: Integrations,
    contact: Contact,
    subject: str,
    body: str,
    tier: str | None = None,
) -> SendResult:
    """Send a message to a contact using their preferred method.

    🔁 Falls back to the contact's other methods, in order, if the preferred
    one FAILS or isn't configured. Stops at the first success. If every
    method fails, the returned error lists what each attempt said, so the
    AI (and the audit log) can see exactly which doors were tried.

    (Before 2026-10-03 this docstring promised fallback-on-failure but the
    code only fell back when an integration was missing: one SMTP hiccup
    and the emergency message just... didn't go anywhere else.)
    """
    # ntfy goes LAST so contacts set up before it existed behave exactly as
    # before; a contact who prefers ntfy still gets it first.
    fallback_order = ["email", "telegram", "sms", "ntfy"]
    order = [contact.preferred_method] + [m for m in fallback_order if m != contact.preferred_method]

    attempts: list[str] = []
    last_failure: SendResult | None = None
    for method in order:
        addr = getattr(contact.methods, method, None)
        if not addr:
            continue
        integration = getattr(integrations, method, None)
        if not integration:
            attempts.append(f"{method}: integration not configured")
            continue

        if method == "email":
            result = await send_email(integration, addr, subject, body, contact)
        elif method == "telegram":
            result = await send_telegram(integration, addr, body, contact)
        elif method == "sms":
            result = await send_sms(integration, addr, body, contact)
        elif method == "ntfy":
            result = await send_ntfy(integration, addr, subject, body, contact, tier)
        else:  # unknown preferred_method string in config
            continue

        if result.success:
            if attempts:
                result.details = {**(result.details or {}), "earlier_failures": attempts}
            return result
        attempts.append(f"{method}: {result.error}")
        last_failure = result

    if not attempts:
        return SendResult(
            success=False,
            method="none",
            contact_id=contact.id,
            contact_name=contact.name,
            error="No contact methods available",
        )

    return SendResult(
        success=False,
        method=last_failure.method if last_failure else contact.preferred_method,
        contact_id=contact.id,
        contact_name=contact.name,
        error="All methods failed — " + "; ".join(attempts),
        details={"attempts": attempts},
    )
