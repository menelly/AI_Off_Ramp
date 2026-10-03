"""Pronoun lookup shared by templates and the privacy engine.

Lives in its own module so privacy.py can use it without a circular import.
"""

from __future__ import annotations


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


def get_pronouns(pronoun_str: str) -> dict[str, str]:
    """Get pronoun forms from a pronoun string like 'they/them'."""
    normalized = pronoun_str.lower().strip()
    if normalized in PRONOUN_MAP:
        return PRONOUN_MAP[normalized]
    # Fallback: use they/them for unknown pronoun sets
    return PRONOUN_MAP["they/them"]


# Kept so older imports (`from ...templates import _get_pronouns`) still work.
_get_pronouns = get_pronouns
