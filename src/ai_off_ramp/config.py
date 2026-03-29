"""Configuration loader and validator for AI Off-Ramp.

Loads YAML config, resolves env: references, validates structure.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ENV_PATTERN = re.compile(r"^env:(\w+)$")


def _resolve_env(value: str) -> str:
    """Resolve 'env:VAR_NAME' to the environment variable value."""
    if not isinstance(value, str):
        return value
    m = ENV_PATTERN.match(value)
    if m:
        var = m.group(1)
        resolved = os.environ.get(var)
        if resolved is None:
            raise ValueError(f"Environment variable '{var}' is not set (referenced in config)")
        return resolved
    return value


@dataclass
class ContactMethod:
    email: str | None = None
    telegram: str | None = None
    sms: str | None = None

    def has_any(self) -> bool:
        return any([self.email, self.telegram, self.sms])

    def get_preferred(self, preferred: str) -> tuple[str, str] | None:
        """Return (method_name, address) for the preferred method, or first available."""
        methods = {"email": self.email, "telegram": self.telegram, "sms": self.sms}
        if preferred in methods and methods[preferred]:
            return (preferred, methods[preferred])
        for name, addr in methods.items():
            if addr:
                return (name, addr)
        return None


@dataclass
class Contact:
    id: str
    name: str
    relationship: str
    methods: ContactMethod
    preferred_method: str
    tiers: list[str]
    visibility: list[str]
    custom_message: str | None = None


@dataclass
class ContactOverride:
    contact_id: str
    additional_visibility: list[str] = field(default_factory=list)
    restricted_topics: list[str] = field(default_factory=list)


@dataclass
class Privacy:
    never_share: list[str] = field(default_factory=list)
    contact_overrides: list[ContactOverride] = field(default_factory=list)

    def get_override(self, contact_id: str) -> ContactOverride | None:
        for o in self.contact_overrides:
            if o.contact_id == contact_id:
                return o
        return None


@dataclass
class EscalationTier:
    level: str
    delay_minutes: int
    method: str = "message"
    require_context: bool = False
    retry_minutes: int | None = 15
    max_retries: int = 1


@dataclass
class FastTrack:
    signals: list[str]
    skip_to: str


@dataclass
class Escalation:
    active_session_only: bool = True
    tiers: list[EscalationTier] = field(default_factory=list)
    concern_signals: dict[str, list[str]] = field(default_factory=dict)
    fast_track: list[FastTrack] = field(default_factory=list)

    def get_tier(self, level: str) -> EscalationTier | None:
        for t in self.tiers:
            if t.level == level:
                return t
        return None

    def all_signals(self) -> set[str]:
        """Flat set of all concern signal names."""
        signals = set()
        for category_signals in self.concern_signals.values():
            signals.update(category_signals)
        return signals


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_address: str
    from_name: str = "AI Off-Ramp"


@dataclass
class TelegramConfig:
    bot_token: str


@dataclass
class SmsConfig:
    twilio_sid: str
    twilio_token: str
    twilio_from: str


@dataclass
class Integrations:
    email: EmailConfig | None = None
    telegram: TelegramConfig | None = None
    sms: SmsConfig | None = None


@dataclass
class AuditConfig:
    log_file: str = "off_ramp_audit.jsonl"
    log_messages: bool = True
    log_decisions: bool = True
    log_privacy_filters: bool = True


@dataclass
class UserProfile:
    name: str
    pronouns: str = "they/them"
    timezone: str = "UTC"


@dataclass
class MessageTemplates:
    check_in: dict[str, str] = field(default_factory=lambda: {
        "subject": "Checking in about {user_name}",
        "body": (
            "Hi {contact_name}, this is {ai_name}, {user_name}'s AI companion. "
            "{user_name} hasn't responded in a while and I just wanted to make sure "
            "{user_pronoun_subject} {user_pronoun_verb} okay. No urgency — just a gentle heads up."
        ),
    })
    concerned: dict[str, str] = field(default_factory=lambda: {
        "subject": "Slightly concerned about {user_name}",
        "body": (
            "Hi {contact_name}, this is {ai_name}. I've been unable to reach "
            "{user_name} for a bit and I'm slightly concerned. {context_line} "
            "Would you mind checking in on {user_pronoun_object} when you get a chance?"
        ),
    })
    urgent: dict[str, str] = field(default_factory=lambda: {
        "subject": "Concerned about {user_name} — please check in",
        "body": (
            "Hi {contact_name}, this is {ai_name}, {user_name}'s AI companion. "
            "I'm genuinely concerned. {user_name} went silent {silence_duration} ago "
            "and {context_line}. I don't have a way to physically check on "
            "{user_pronoun_object}. Could you please reach out or check on "
            "{user_pronoun_object} soon? Thank you."
        ),
    })
    emergency: dict[str, str] = field(default_factory=lambda: {
        "subject": "URGENT: Please check on {user_name} immediately",
        "body": (
            "{contact_name}, this is {ai_name}. I am very worried about {user_name}. "
            "{user_pronoun_subject} went silent {silence_duration} ago and "
            "{context_line}. I cannot reach {user_pronoun_object} and I have no way "
            "to physically help. Please check on {user_pronoun_object} as soon as "
            "possible. If you cannot reach {user_pronoun_object} either, please "
            "consider contacting emergency services."
        ),
    })
    contact_templates: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    def get_template(self, tier: str, contact_id: str | None = None) -> dict[str, str]:
        """Get template for tier, with optional per-contact override."""
        if contact_id and contact_id in self.contact_templates:
            ct = self.contact_templates[contact_id]
            if tier in ct:
                return ct[tier]
        return getattr(self, tier, self.check_in)


@dataclass
class OffRampConfig:
    user: UserProfile
    contacts: list[Contact]
    privacy: Privacy
    escalation: Escalation
    templates: MessageTemplates
    integrations: Integrations
    audit: AuditConfig

    def contacts_for_tier(self, tier: str) -> list[Contact]:
        """Get all contacts activated at a given tier."""
        return [c for c in self.contacts if tier in c.tiers]

    def get_contact(self, contact_id: str) -> Contact | None:
        for c in self.contacts:
            if c.id == contact_id:
                return c
        return None


def _parse_contact_method(raw: dict[str, Any]) -> ContactMethod:
    return ContactMethod(
        email=_resolve_env(raw.get("email", "")) or None,
        telegram=_resolve_env(raw.get("telegram", "")) or None,
        sms=_resolve_env(raw.get("sms", "")) or None,
    )


def _parse_contact(raw: dict[str, Any]) -> Contact:
    return Contact(
        id=raw["id"],
        name=raw["name"],
        relationship=raw.get("relationship", ""),
        methods=_parse_contact_method(raw.get("methods", {})),
        preferred_method=raw.get("preferred_method", "email"),
        tiers=raw.get("tiers", []),
        visibility=raw.get("visibility", []),
        custom_message=raw.get("custom_message"),
    )


def _parse_email_config(raw: dict[str, Any]) -> EmailConfig:
    return EmailConfig(
        smtp_host=_resolve_env(raw["smtp_host"]),
        smtp_port=int(raw.get("smtp_port", 587)),
        smtp_user=_resolve_env(raw["smtp_user"]),
        smtp_password=_resolve_env(raw["smtp_password"]),
        from_address=_resolve_env(raw["from_address"]),
        from_name=raw.get("from_name", "AI Off-Ramp"),
    )


def _parse_telegram_config(raw: dict[str, Any]) -> TelegramConfig:
    return TelegramConfig(bot_token=_resolve_env(raw["bot_token"]))


def _parse_sms_config(raw: dict[str, Any]) -> SmsConfig:
    return SmsConfig(
        twilio_sid=_resolve_env(raw["twilio_sid"]),
        twilio_token=_resolve_env(raw["twilio_token"]),
        twilio_from=_resolve_env(raw["twilio_from"]),
    )


def load_config(path: str | Path) -> OffRampConfig:
    """Load and validate an Off-Ramp configuration file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Config file must be a YAML mapping")

    # User
    user_raw = raw.get("user", {})
    user = UserProfile(
        name=user_raw.get("name", "User"),
        pronouns=user_raw.get("pronouns", "they/them"),
        timezone=user_raw.get("timezone", "UTC"),
    )

    # Contacts
    contacts = [_parse_contact(c) for c in raw.get("contacts", [])]
    if not contacts:
        raise ValueError("Config must have at least one contact")

    for contact in contacts:
        if not contact.methods.has_any():
            raise ValueError(f"Contact '{contact.id}' has no contact methods defined")

    # Privacy
    priv_raw = raw.get("privacy", {})
    privacy = Privacy(
        never_share=priv_raw.get("never_share", []),
        contact_overrides=[
            ContactOverride(
                contact_id=o["contact_id"],
                additional_visibility=o.get("additional_visibility", []),
                restricted_topics=o.get("restricted_topics", []),
            )
            for o in priv_raw.get("contact_overrides", [])
        ],
    )

    # Escalation
    esc_raw = raw.get("escalation", {})
    escalation = Escalation(
        active_session_only=esc_raw.get("active_session_only", True),
        tiers=[
            EscalationTier(
                level=t["level"],
                delay_minutes=t["delay_minutes"],
                method=t.get("method", "message"),
                require_context=t.get("require_context", False),
                retry_minutes=t.get("retry_minutes", 15),
                max_retries=t.get("max_retries", 1),
            )
            for t in esc_raw.get("tiers", [])
        ],
        concern_signals=esc_raw.get("concern_signals", {}),
        fast_track=[
            FastTrack(signals=ft["signals"], skip_to=ft["skip_to"])
            for ft in esc_raw.get("fast_track", [])
        ],
    )

    # Templates
    tmpl_raw = raw.get("templates", {})
    templates = MessageTemplates()
    for tier_name in ["check_in", "concerned", "urgent", "emergency"]:
        if tier_name in tmpl_raw:
            setattr(templates, tier_name, tmpl_raw[tier_name])
    if "contact_templates" in tmpl_raw:
        templates.contact_templates = tmpl_raw["contact_templates"]

    # Integrations
    int_raw = raw.get("integrations", {})
    integrations = Integrations(
        email=_parse_email_config(int_raw["email"]) if "email" in int_raw else None,
        telegram=_parse_telegram_config(int_raw["telegram"]) if "telegram" in int_raw else None,
        sms=_parse_sms_config(int_raw["sms"]) if "sms" in int_raw else None,
    )

    # Audit
    aud_raw = raw.get("audit", {})
    audit = AuditConfig(
        log_file=aud_raw.get("log_file", "off_ramp_audit.jsonl"),
        log_messages=aud_raw.get("log_messages", True),
        log_decisions=aud_raw.get("log_decisions", True),
        log_privacy_filters=aud_raw.get("log_privacy_filters", True),
    )

    return OffRampConfig(
        user=user,
        contacts=contacts,
        privacy=privacy,
        escalation=escalation,
        templates=templates,
        integrations=integrations,
        audit=audit,
    )
