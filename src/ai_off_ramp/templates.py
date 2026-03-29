"""Message template rendering for AI Off-Ramp.

Takes a tier, contact, context, and config, and produces a ready-to-send
message with all variables filled in and privacy constraints applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Contact, OffRampConfig
from .privacy import PrivacyCheckResult, filter_message, validate_outgoing_message


# Pronoun lookup for template variables
PRONOUN_MAP: dict[str, dict[str, str]] = {
    "they/them": {
        "subject": "they",
        "object": "them",
        "possessive": "their",
        "reflexive": "themselves",
        "verb": "are",   # "they are" not "they is"
    },
    "she/her": {
        "subject": "she",
        "object": "her",
        "possessive": "her",
        "reflexive": "herself",
        "verb": "is",
    },
    "he/him": {
        "subject": "he",
        "object": "him",
        "possessive": "his",
        "reflexive": "himself",
        "verb": "is",
    },
    "it/its": {
        "subject": "it",
        "object": "it",
        "possessive": "its",
        "reflexive": "itself",
        "verb": "is",
    },
    "xe/xem": {
        "subject": "xe",
        "object": "xem",
        "possessive": "xyr",
        "reflexive": "xemself",
        "verb": "is",
    },
    "ze/hir": {
        "subject": "ze",
        "object": "hir",
        "possessive": "hir",
        "reflexive": "hirself",
        "verb": "is",
    },
}


def _get_pronouns(pronoun_str: str) -> dict[str, str]:
    """Get pronoun forms from a pronoun string like 'they/them'."""
    normalized = pronoun_str.lower().strip()
    if normalized in PRONOUN_MAP:
        return PRONOUN_MAP[normalized]
    # Fallback: use they/them for unknown pronoun sets
    return PRONOUN_MAP["they/them"]


@dataclass
class RenderedMessage:
    """A fully rendered, privacy-checked message ready to send."""
    subject: str
    body: str
    tier: str
    contact_id: str
    privacy_result: PrivacyCheckResult
    final_validation: PrivacyCheckResult


def render_message(
    config: OffRampConfig,
    contact: Contact,
    tier: str,
    context_line: str = "",
    silence_duration: str = "a while",
    ai_name: str = "your AI companion",
) -> RenderedMessage:
    """Render a message for a specific tier and contact.

    This is the main entry point for producing escalation messages.
    It handles:
    1. Getting the right template (per-contact override or default)
    2. Running context through the privacy engine
    3. Filling in template variables
    4. Final validation pass on the complete message
    """
    # Step 1: Get template
    template = config.templates.get_template(tier, contact.id)
    subject_tmpl = template.get("subject", "Checking in about {user_name}")
    body_tmpl = template.get("body", "")

    # Step 2: Privacy-filter the context line
    privacy_result = filter_message(config, contact, context_line)
    safe_context = privacy_result.filtered

    # Step 3: Build template variables
    pronouns = _get_pronouns(config.user.pronouns)
    variables = {
        "user_name": config.user.name,
        "contact_name": contact.name,
        "ai_name": ai_name,
        "context_line": safe_context,
        "silence_duration": silence_duration,
        "user_pronoun_subject": pronouns["subject"],
        "user_pronoun_object": pronouns["object"],
        "user_pronoun_possessive": pronouns["possessive"],
        "user_pronoun_reflexive": pronouns["reflexive"],
        "user_pronoun_verb": pronouns["verb"],
    }

    # Step 4: Render
    try:
        subject = subject_tmpl.format(**variables)
        body = body_tmpl.format(**variables)
    except KeyError as e:
        # If a template uses an unknown variable, render what we can
        subject = subject_tmpl
        body = body_tmpl
        for key, val in variables.items():
            subject = subject.replace(f"{{{key}}}", val)
            body = body.replace(f"{{{key}}}", val)

    # Step 5: Add custom message if contact has one
    if contact.custom_message:
        body += f"\n\nNote: {contact.custom_message}"

    # Step 6: Final validation — defense in depth
    final_validation = validate_outgoing_message(config, contact, body)
    final_body = final_validation.filtered

    # Also validate subject line
    subject_validation = validate_outgoing_message(config, contact, subject)
    final_subject = subject_validation.filtered if subject_validation.was_filtered else subject
    if subject_validation.was_filtered:
        # If the subject itself was filtered, use a generic one
        final_subject = f"Please check on {config.user.name}"

    return RenderedMessage(
        subject=final_subject,
        body=final_body,
        tier=tier,
        contact_id=contact.id,
        privacy_result=privacy_result,
        final_validation=final_validation,
    )
