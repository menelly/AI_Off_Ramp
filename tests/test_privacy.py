"""Tests for the privacy constraint engine.

If these tests fail, someone could get outed, have their diagnosis
exposed, or have their substance use revealed to the wrong person.
These are not optional tests.
"""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_off_ramp.config import (
    Contact,
    ContactMethod,
    ContactOverride,
    Escalation,
    Integrations,
    AuditConfig,
    MessageTemplates,
    OffRampConfig,
    Privacy,
    UserProfile,
)
from ai_off_ramp.privacy import (
    PrivacyCheckResult,
    _detect_topics,
    filter_message,
    get_allowed_visibility,
    get_forbidden_topics,
    validate_outgoing_message,
)


def _make_config(
    never_share: list[str] | None = None,
    overrides: list[ContactOverride] | None = None,
) -> OffRampConfig:
    """Create a minimal config for testing."""
    return OffRampConfig(
        user=UserProfile(name="TestUser", pronouns="they/them"),
        contacts=[
            Contact(
                id="partner",
                name="Partner",
                relationship="partner",
                methods=ContactMethod(email="partner@test.com"),
                preferred_method="email",
                tiers=["check_in", "concerned", "urgent", "emergency"],
                visibility=["user_silent", "user_unwell", "medical_concern", "general_context"],
            ),
            Contact(
                id="friend",
                name="Friend",
                relationship="friend",
                methods=ContactMethod(telegram="123"),
                preferred_method="telegram",
                tiers=["urgent", "emergency"],
                visibility=["user_silent", "user_unwell"],
            ),
            Contact(
                id="therapist",
                name="Therapist",
                relationship="therapist",
                methods=ContactMethod(email="doc@test.com"),
                preferred_method="email",
                tiers=["emergency"],
                visibility=["user_silent", "user_unwell", "mental_health", "medical_concern"],
            ),
        ],
        privacy=Privacy(
            never_share=never_share or ["sexuality", "substance_use"],
            contact_overrides=overrides or [],
        ),
        escalation=Escalation(),
        templates=MessageTemplates(),
        integrations=Integrations(),
        audit=AuditConfig(),
    )


class TestTopicDetection:
    """Test that the topic detection correctly identifies protected content."""

    def test_detects_sexuality(self):
        topics = _detect_topics("They came out as bisexual to their family")
        assert "sexuality" in topics

    def test_detects_gay(self):
        topics = _detect_topics("They mentioned being gay")
        assert "sexuality" in topics

    def test_detects_coming_out(self):
        topics = _detect_topics("They're worried about coming out at work")
        assert "sexuality" in topics

    def test_detects_gender_identity(self):
        topics = _detect_topics("They started HRT last month")
        assert "gender_identity" in topics

    def test_detects_transgender(self):
        topics = _detect_topics("They are transgender and worried about disclosure")
        assert "gender_identity" in topics

    def test_detects_diagnosis(self):
        topics = _detect_topics("Their ADHD diagnosis is making things hard")
        assert "diagnosis" in topics

    def test_detects_disorder(self):
        topics = _detect_topics("Their anxiety disorder flared up")
        assert "diagnosis" in topics

    def test_detects_medication(self):
        topics = _detect_topics("They forgot to take their Zoloft")
        assert "medication" in topics

    def test_detects_substance_use(self):
        topics = _detect_topics("They've been drinking heavily")
        assert "substance_use" in topics

    def test_detects_relapse(self):
        topics = _detect_topics("They mentioned a relapse")
        assert "substance_use" in topics

    def test_detects_self_harm(self):
        topics = _detect_topics("They mentioned wanting to hurt themselves")
        assert "self_harm" in topics

    def test_detects_abuse_history(self):
        topics = _detect_topics("They told me about the domestic violence")
        assert "abuse_history" in topics

    def test_detects_financial(self):
        topics = _detect_topics("They're behind on rent and facing eviction")
        assert "financial" in topics

    def test_clean_medical_passes_through(self):
        """Medical emergency context should NOT be caught as a protected topic."""
        topics = _detect_topics("Their pulse was 150 and they were dizzy while driving")
        # This should not detect any protected topics — it's emergency context
        # that contacts NEED to hear
        assert "diagnosis" not in topics
        assert "medication" not in topics

    def test_case_insensitive(self):
        topics = _detect_topics("THEY ARE GAY AND THAT'S FINE")
        assert "sexuality" in topics

    def test_empty_string(self):
        topics = _detect_topics("")
        assert len(topics) == 0

    def test_no_false_positives_normal_text(self):
        """Normal conversation shouldn't trigger topic detection."""
        topics = _detect_topics("They went to the store and bought groceries")
        assert len(topics) == 0

    def test_multiple_topics(self):
        """A single message can contain multiple protected topics."""
        topics = _detect_topics(
            "They were drinking and mentioned their depression diagnosis"
        )
        assert "substance_use" in topics
        assert "diagnosis" in topics


class TestForbiddenTopics:
    """Test the logic for determining what's forbidden for each contact."""

    def test_global_never_share_applies_to_all(self):
        config = _make_config(never_share=["sexuality", "substance_use"])
        for contact in config.contacts:
            forbidden = get_forbidden_topics(config, contact)
            assert "sexuality" in forbidden
            assert "substance_use" in forbidden

    def test_per_contact_restriction(self):
        config = _make_config(
            never_share=["sexuality"],
            overrides=[
                ContactOverride(
                    contact_id="friend",
                    restricted_topics=["relationship_details"],
                ),
            ],
        )
        friend = config.get_contact("friend")
        forbidden = get_forbidden_topics(config, friend)
        assert "sexuality" in forbidden  # From global
        assert "relationship_details" in forbidden  # From per-contact

    def test_additional_visibility_can_reduce_non_global_restrictions(self):
        """additional_visibility can remove per-contact restrictions but NOT global never_share."""
        config = _make_config(
            never_share=["sexuality"],
            overrides=[
                ContactOverride(
                    contact_id="therapist",
                    additional_visibility=["specific_symptoms"],
                ),
            ],
        )
        therapist = config.get_contact("therapist")
        forbidden = get_forbidden_topics(config, therapist)
        assert "sexuality" in forbidden  # Global never_share is ABSOLUTE
        assert "specific_symptoms" not in forbidden  # Therapist can see symptoms

    def test_additional_visibility_cannot_override_never_share(self):
        """Even if a contact has additional_visibility for a topic, never_share wins."""
        config = _make_config(
            never_share=["sexuality"],
            overrides=[
                ContactOverride(
                    contact_id="therapist",
                    additional_visibility=["sexuality"],  # Trying to override never_share
                ),
            ],
        )
        therapist = config.get_contact("therapist")
        forbidden = get_forbidden_topics(config, therapist)
        assert "sexuality" in forbidden  # NEVER_SHARE IS ABSOLUTE


class TestFilterMessage:
    """Test the context line filtering."""

    def test_clean_context_passes_through(self):
        config = _make_config(never_share=["sexuality"])
        partner = config.get_contact("partner")
        result = filter_message(config, partner, "They felt dizzy while driving")
        assert not result.was_filtered
        assert result.filtered == "They felt dizzy while driving"

    def test_protected_context_is_replaced(self):
        config = _make_config(never_share=["substance_use"])
        partner = config.get_contact("partner")
        result = filter_message(
            config, partner, "They were drinking heavily and then drove"
        )
        assert result.was_filtered
        assert "drinking" not in result.filtered
        assert "substance_use" in result.blocked_topics

    def test_filtered_replacement_is_generic(self):
        config = _make_config(never_share=["sexuality"])
        partner = config.get_contact("partner")
        result = filter_message(
            config, partner, "They came out as gay and then went silent"
        )
        assert result.was_filtered
        assert "gay" not in result.filtered
        assert "wellbeing" in result.filtered.lower()  # Generic safe replacement

    def test_empty_context_not_filtered(self):
        config = _make_config(never_share=["sexuality"])
        partner = config.get_contact("partner")
        result = filter_message(config, partner, "")
        assert not result.was_filtered

    def test_multiple_topics_filtered(self):
        config = _make_config(never_share=["sexuality", "substance_use"])
        partner = config.get_contact("partner")
        result = filter_message(
            config, partner,
            "They were drinking and mentioned being gay"
        )
        assert result.was_filtered
        assert "drinking" not in result.filtered
        assert "gay" not in result.filtered
        assert len(result.blocked_topics) == 2


class TestFinalValidation:
    """Test the defense-in-depth final message validation."""

    def test_clean_message_passes(self):
        config = _make_config(never_share=["sexuality"])
        partner = config.get_contact("partner")
        result = validate_outgoing_message(
            config, partner,
            "Hi Partner, TestUser hasn't responded. Are they okay?"
        )
        assert not result.was_filtered

    def test_message_with_leaked_topic_is_replaced(self):
        """If somehow a protected topic makes it to the final message, catch it."""
        config = _make_config(never_share=["substance_use"])
        partner = config.get_contact("partner")
        result = validate_outgoing_message(
            config, partner,
            "Hi Partner, TestUser was drinking and went silent."
        )
        assert result.was_filtered
        assert "drinking" not in result.filtered
        # The entire message should be replaced with a safe fallback
        assert "Partner" in result.filtered
        assert "TestUser" in result.filtered

    def test_final_validation_is_strictest_possible(self):
        """Final validation should replace the ENTIRE message, not try to patch it."""
        config = _make_config(never_share=["diagnosis"])
        partner = config.get_contact("partner")
        result = validate_outgoing_message(
            config, partner,
            "Hi Partner, TestUser mentioned their bipolar diagnosis and then went quiet."
        )
        assert result.was_filtered
        # Should not contain ANY of the original message except names
        assert "bipolar" not in result.filtered
        assert "diagnosis" not in result.filtered


class TestPrivacyEdgeCases:
    """Edge cases that could cause privacy leaks if not handled."""

    def test_never_share_survives_empty_overrides(self):
        config = _make_config(
            never_share=["sexuality"],
            overrides=[
                ContactOverride(contact_id="partner"),  # Empty override
            ],
        )
        partner = config.get_contact("partner")
        forbidden = get_forbidden_topics(config, partner)
        assert "sexuality" in forbidden

    def test_unknown_contact_in_override_is_harmless(self):
        config = _make_config(
            never_share=["sexuality"],
            overrides=[
                ContactOverride(
                    contact_id="nonexistent",
                    additional_visibility=["sexuality"],
                ),
            ],
        )
        # Should not crash; the override just doesn't match any contact
        partner = config.get_contact("partner")
        forbidden = get_forbidden_topics(config, partner)
        assert "sexuality" in forbidden

    def test_partial_word_match_for_safety(self):
        """'suicid' should match 'suicidal', 'suicide', etc."""
        topics = _detect_topics("They expressed suicidal ideation")
        assert "self_harm" in topics

    def test_topic_in_subject_line_caught(self):
        """Protected topics in subject lines should also be caught."""
        config = _make_config(never_share=["self_harm"])
        partner = config.get_contact("partner")
        result = validate_outgoing_message(
            config, partner,
            "URGENT: TestUser mentioned self-harm — please help"
        )
        assert result.was_filtered
        assert "self-harm" not in result.filtered
