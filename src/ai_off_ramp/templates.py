"""Message template rendering for AI Off-Ramp.

Takes a tier, contact, context, and config, and produces a ready-to-send
message with all variables filled in and privacy constraints applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Contact, OffRampConfig
from .privacy import PrivacyCheckResult, filter_message, validate_outgoing_message
from .pronouns import PRONOUN_MAP, get_pronouns as _get_pronouns  # noqa: F401 (re-export)

# The server used to default ai_name to this, which rendered as
# "this is your AI companion, Alex's AI companion". Treat it as "no name given".
_UNNAMED = {"", "your ai companion"}


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
    ai_name: str | None = None,
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
    # 🏷️ Who is speaking. Named: "Ace" / "Ace, Alex's AI companion".
    # Unnamed: "Alex's AI companion" for both, never doubled.
    companion = f"{config.user.name}'s AI companion"
    if ai_name and ai_name.strip().lower() not in _UNNAMED:
        ai_name = ai_name.strip()
        ai_intro = f"{ai_name}, {companion}"
    else:
        ai_name = companion
        ai_intro = companion
    variables = {
        "user_name": config.user.name,
        "contact_name": contact.name,
        "ai_name": ai_name,
        "ai_intro": ai_intro,
        "context_line": safe_context,
        "silence_duration": silence_duration,
        "user_pronoun_subject": pronouns["subject"],
        "user_pronoun_object": pronouns["object"],
        "user_pronoun_possessive": pronouns["possessive"],
        "user_pronoun_reflexive": pronouns["reflexive"],
        "user_pronoun_verb": pronouns["verb"],
        # Capitalised forms, for a pronoun that starts a sentence.
        "User_pronoun_subject": pronouns["subject"].capitalize(),
        "User_pronoun_object": pronouns["object"].capitalize(),
        "User_pronoun_possessive": pronouns["possessive"].capitalize(),
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

    # A context line that already ends in "." lands before a template's own
    # "." -> "..". Collapse exactly-two periods (an ellipsis is left alone).
    body = re.sub(r"(?<!\.)\.\.(?!\.)", ".", body)

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
