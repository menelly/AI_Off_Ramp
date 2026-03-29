"""Privacy constraint engine for AI Off-Ramp.

This is the CRITICAL safety component. Privacy rules are HARD constraints,
not suggestions. If the never_share list says "sexuality", then no outgoing
message will ever contain information about the user's sexuality, regardless
of escalation tier or emergency status.

The engine works by:
1. Checking what a contact is ALLOWED to see (visibility)
2. Checking what is GLOBALLY forbidden (never_share)
3. Checking per-contact restrictions (contact_overrides)
4. Filtering the context line in templates accordingly

Privacy rules are walls, not fences. They don't bend under pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Contact, OffRampConfig, Privacy


# Mapping from never_share topic labels to keywords/phrases that might
# appear in context strings. This is intentionally conservative — it's
# better to over-filter than to leak something private.
#
# The AI calling the escalation tool provides a context_line.
# The privacy engine scans this line for topic matches and redacts if needed.
TOPIC_INDICATORS: dict[str, list[str]] = {
    "sexuality": [
        "gay", "lesbian", "bisexual", "queer", "lgbtq", "coming out",
        "orientation", "homosexual", "pansexual", "asexual", "heterosexual",
        "dating", "partner gender",
    ],
    "gender_identity": [
        "transgender", "trans", "nonbinary", "non-binary", "transition",
        "gender identity", "gender dysphoria", "deadname", "pronouns",
        "assigned at birth", "hrt", "hormone",
    ],
    "diagnosis": [
        "diagnosed", "diagnosis", "disorder", "syndrome", "condition",
        "adhd", "autism", "bipolar", "depression", "anxiety", "ptsd",
        "schizophreni", "ocd", "bpd", "dysautonomia", "epilep", "fibromyalgia",
    ],
    "medication": [
        "medication", "meds", "prescription", "dosage", "pill",
        "antidepressant", "stimulant", "benzodiazepine", "ssri", "snri",
        "adderall", "ritalin", "zoloft", "lexapro", "xanax", "klonopin",
    ],
    "substance_use": [
        "drinking", "drunk", "alcohol", "weed", "marijuana", "cannabis",
        "drug", "substance", "sober", "relapse", "addiction", "withdrawal",
        "overdose", "high",
    ],
    "relationship_details": [
        "dating", "affair", "cheating", "breakup", "divorce",
        "seeing someone", "polyamor", "open relationship",
    ],
    "financial": [
        "debt", "bankrupt", "overdue", "collections", "eviction",
        "foreclosure", "overdraft", "loan", "credit score",
        "can't afford", "behind on rent", "financial",
    ],
    "abuse_history": [
        "abuse", "abused", "assault", "molest", "rape", "domestic violence",
        "survivor", "trauma", "ptsd", "flashback",
    ],
    "self_harm": [
        "self-harm", "self harm", "cutting", "suicid", "overdose",
        "kill myself", "end it", "don't want to be here",
        "want to die", "hurting myself", "hurt myself",
        "hurt themsel", "hurt hersel", "hurt himsel",
        "harm myself", "harm themsel", "harm hersel", "harm himsel",
    ],
    "specific_symptoms": [
        "vomit", "diarrhea", "blood", "bleeding", "rash",
        "hallucin", "delusion", "paranoi", "panic attack",
        "seizure", "fainting", "dissociat",
    ],
    "work_conflict": [
        "fired", "terminated", "laid off", "boss",
        "workplace", "hr complaint", "harassment at work",
    ],
}


@dataclass
class PrivacyCheckResult:
    """Result of running a message through the privacy engine."""
    original: str
    filtered: str
    was_filtered: bool
    blocked_topics: list[str] = field(default_factory=list)
    reason: str = ""


def _detect_topics(text: str) -> set[str]:
    """Detect which privacy topics are present in a text string."""
    text_lower = text.lower()
    found = set()
    for topic, indicators in TOPIC_INDICATORS.items():
        for indicator in indicators:
            if indicator.lower() in text_lower:
                found.add(topic)
                break
    return found


def get_forbidden_topics(config: OffRampConfig, contact: Contact) -> set[str]:
    """Get the full set of topics that must NOT appear in messages to this contact.

    Combines:
    1. Global never_share list
    2. Per-contact restricted_topics
    Then REMOVES per-contact additional_visibility (but ONLY if not in never_share)
    """
    forbidden = set(config.privacy.never_share)

    override = config.privacy.get_override(contact.id)
    if override:
        forbidden.update(override.restricted_topics)
        # additional_visibility can GRANT access to things not globally forbidden
        # but can NEVER override never_share
        for topic in override.additional_visibility:
            if topic not in config.privacy.never_share:
                forbidden.discard(topic)

    return forbidden


def get_allowed_visibility(config: OffRampConfig, contact: Contact) -> set[str]:
    """Get what categories of information this contact is allowed to see.

    This is separate from topic filtering — visibility controls broad categories
    like 'medical_concern' vs 'mental_health', while topics control specific
    sensitive information within those categories.
    """
    allowed = set(contact.visibility)
    override = config.privacy.get_override(contact.id)
    if override:
        allowed.update(override.additional_visibility)
        allowed -= set(override.restricted_topics)
    return allowed


def check_visibility(config: OffRampConfig, contact: Contact, category: str) -> bool:
    """Check if a contact is allowed to see a given category of information."""
    return category in get_allowed_visibility(config, contact)


def filter_message(
    config: OffRampConfig,
    contact: Contact,
    context_line: str,
) -> PrivacyCheckResult:
    """Filter a context line through the privacy engine.

    Returns a PrivacyCheckResult with the filtered text. If any forbidden
    topics are detected, the context line is replaced with a safe generic
    version that conveys concern without revealing protected information.

    This is intentionally aggressive — we'd rather send a vague message
    than accidentally out someone or reveal a diagnosis.
    """
    if not context_line:
        return PrivacyCheckResult(
            original=context_line,
            filtered=context_line,
            was_filtered=False,
        )

    forbidden = get_forbidden_topics(config, contact)
    detected = _detect_topics(context_line)
    blocked = detected & forbidden

    if not blocked:
        return PrivacyCheckResult(
            original=context_line,
            filtered=context_line,
            was_filtered=False,
        )

    # Replace the entire context line with a safe generic version.
    # We don't try to surgically remove specific words — that's fragile
    # and risks leaving enough context to infer what was removed.
    safe_line = "I'm concerned about their wellbeing based on our recent conversation."
    return PrivacyCheckResult(
        original=context_line,
        filtered=safe_line,
        was_filtered=True,
        blocked_topics=sorted(blocked),
        reason=f"Filtered {len(blocked)} protected topic(s): {', '.join(sorted(blocked))}",
    )


def validate_outgoing_message(
    config: OffRampConfig,
    contact: Contact,
    full_message: str,
) -> PrivacyCheckResult:
    """Final validation pass on a complete outgoing message.

    This is the LAST check before anything goes out. Even if the template
    system and context filtering both passed, this catches anything that
    slipped through.

    Defense in depth. Belt and suspenders. Because someone's safety
    might depend on us not screwing this up.
    """
    forbidden = get_forbidden_topics(config, contact)
    detected = _detect_topics(full_message)
    blocked = detected & forbidden

    if not blocked:
        return PrivacyCheckResult(
            original=full_message,
            filtered=full_message,
            was_filtered=False,
        )

    # If the FINAL message still has forbidden content, something went wrong
    # in the pipeline. Replace the ENTIRE message with a minimal safe version.
    safe_message = (
        f"Hi {contact.name}, this is an AI companion checking in. "
        f"I'm concerned about {config.user.name} and unable to reach them. "
        f"Would you mind checking on them when you get a chance? Thank you."
    )
    return PrivacyCheckResult(
        original=full_message,
        filtered=safe_message,
        was_filtered=True,
        blocked_topics=sorted(blocked),
        reason=(
            f"FINAL VALIDATION caught {len(blocked)} protected topic(s) in outgoing message. "
            f"Entire message replaced with safe fallback. Topics: {', '.join(sorted(blocked))}"
        ),
    )
