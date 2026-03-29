"""Contact method integrations for AI Off-Ramp.

Handles actually sending messages via email, Telegram, or SMS.
Each method is async and returns a send result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Any

import aiohttp

from .config import (
    Contact,
    EmailConfig,
    Integrations,
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


async def send_telegram(
    config: TelegramConfig,
    chat_id: str,
    body: str,
    contact: Contact,
) -> SendResult:
    """Send a Telegram message via Bot API."""
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": body,
        "parse_mode": "HTML",
    }
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


async def send_message(
    integrations: Integrations,
    contact: Contact,
    subject: str,
    body: str,
) -> SendResult:
    """Send a message to a contact using their preferred method.

    Falls back to other available methods if the preferred one fails or
    isn't configured.
    """
    preferred = contact.methods.get_preferred(contact.preferred_method)
    if not preferred:
        return SendResult(
            success=False,
            method="none",
            contact_id=contact.id,
            contact_name=contact.name,
            error="No contact methods available",
        )

    method_name, address = preferred

    if method_name == "email" and integrations.email:
        return await send_email(integrations.email, address, subject, body, contact)
    elif method_name == "telegram" and integrations.telegram:
        return await send_telegram(integrations.telegram, address, body, contact)
    elif method_name == "sms" and integrations.sms:
        return await send_sms(integrations.sms, address, body, contact)

    # Preferred method not configured — try fallbacks
    fallback_order = ["email", "telegram", "sms"]
    for method in fallback_order:
        if method == method_name:
            continue
        addr = getattr(contact.methods, method, None)
        if not addr:
            continue
        integration = getattr(integrations, method, None)
        if not integration:
            continue

        if method == "email":
            return await send_email(integration, addr, subject, body, contact)
        elif method == "telegram":
            return await send_telegram(integration, addr, body, contact)
        elif method == "sms":
            return await send_sms(integration, addr, body, contact)

    return SendResult(
        success=False,
        method=method_name,
        contact_id=contact.id,
        contact_name=contact.name,
        error=f"Integration for '{method_name}' is not configured",
    )
